---
object_key: diagnosis-record
topic: query-contract
title: Diagnosis Record 查询合同
---

# Diagnosis Record 查询合同

## diagnosis-record-query-contract@20260823-0234

**按逻辑记录去重并统一列表与详情职责**

```yaml
record_id: diagnosis-record/query-contract@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - http-api
  - security
  - trace-retrieval
canonical_doc: docs/api.md
```

### 背景

Rescue attempt、反馈追加和延迟写入会产生多个物理 span；API 需要稳定的逻辑记录视图。

### 决策

- 列表按 Job UUID 等过滤条件访问并使用对象时间半开区间；`verdict` 先下推到物理候选查询，再按
  逻辑 UUID 选择最高 attempt、同 attempt 最新反馈版本。因此新 attempt 改判后，旧 attempt 仍可能
  出现在旧 verdict 的筛选结果中。
- 列表返回提升后的紧凑字段，完整 results、evidence、snapshot 和反馈正文只由详情返回。
- 可选 `owner_id` 只做精确过滤；可选实验组使用 canonical token OR 匹配。
- 指定 Task 仍同时应用对象时间范围，物理 period 扫描边界只用于查询规划。

### 保持不变

AgentLens 不通过 space/agent 参数为记录做最终用户授权，也不把无 owner 查询自动收窄。

### 影响与风险

物理查询上限和去重顺序影响深分页；权限必须由上游代理在调用前保证。

### 验证

由记录列表、详情、attempt 去重、反馈版本、owner 和实验组筛选测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## diagnosis-record-query-contract@20260823-1933

**以派生列表属性和最小 Metric body 查询记录**

```yaml
record_id: diagnosis-record/query-contract@20260823-1933
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - diagnosis-record/query-contract@20260823-0234
related_records:
  - diagnosis-record/verdict-derivation@20260823-1933
  - metric-result/evidence@20260823-1933
  - executor/result-parsing@20260823-1933
affected_objects:
  - http-api
  - security
  - trace-retrieval
canonical_doc: docs/api.md
```

### 背景

新结果合同删除单次分数和通过状态，并将顶层 evidence 重定义为 execution。verdict/summary 可从
execution/metrics 派生，但列表仍需不解析完整 body 即可过滤、展示和搜索。

### 决策

- 列表继续提升并读取 verdict、summary、身份、review 和时间字段；删除 displayed_score、
  failed_metric_names 及 `sort_by=displayed_score`。
- 详情返回 `execution`、`metrics` 和 `trace_snapshot`；删除 confidence、顶层 evidence、results 以及
  Metric 的 status/score/raw_value/threshold/passed。
- `record.body` 保存 execution、metrics、trace snapshot、反馈和不可派生元数据，不保存顶层 verdict 与
  summary。详情读取后现场派生二者，不做 body 与提升属性的一致性校验。
- verdict/summary 在写入时从同一 DiagnosisResult 派生并提升为列表属性，继续支持 verdict 下推、摘要
  展示和关键词搜索。
- 现有 `/api/v1` 原地替换合同；不增加 schema version、v2 endpoint、旧 body 适配、旧记录隔离或兼容
  校验。历史 DiagnosisRecord 视为可丢弃数据，不保证新版详情和反馈可读。

### 保持不变

列表的 Job、时间、owner、实验组、attempt/反馈版本去重、物理查询上限和授权边界保持不变；完整详情仍
只由详情接口返回。

### 影响与风险

这是明确的破坏性 API 与持久化变更；旧记录可能继续出现在提升字段列表中但无法读取详情，部署环境可
按自身保留策略清理，AgentLens 不增加迁移逻辑。列表与详情依赖同一派生实现，写入缺陷可能造成展示
差异但读取端不做额外校验。

### 验证

覆盖新版列表/详情/反馈、被删除字段和排序退出、body 不保存派生字段、详情现场派生及现有去重过滤；
不增加旧记录兼容测试。

### 实施说明

已实施：列表、详情和持久化 body 已切换到 metrics/execution 与派生属性合同。

## diagnosis-record-query-contract@20260823-2329

**临时兼容旧 body 的最小 Metric 查询合同**

```yaml
record_id: diagnosis-record/query-contract@20260823-2329
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T23:29+08:00
decided_at: 2026-08-23T23:29+08:00
implemented_at: 2026-08-23T23:29+08:00
supersedes:
  - diagnosis-record/query-contract@20260823-1933
related_records:
  - diagnosis-record/verdict-derivation@20260823-1933
  - metric-result/evidence@20260823-1933
  - executor/result-parsing@20260823-1933
affected_objects:
  - http-api
  - security
  - trace-retrieval
canonical_doc: docs/api.md
```

### 背景

新结果合同上线后，旧版 Trace 查询源在一个月保留期内仍会同时返回旧 `results` body 与新
`metrics/execution` body。普通列表只读取提升属性；category 筛选额外读取同一 Record body 的冻结分类，
响应仍为紧凑提升字段。因此旧记录仍可见；详情和反馈若只识别新 body，会在
用户点击旧记录时失败。HTTP 合同仍应只暴露新结构，不重新引入已删除字段或版本化端点。

### 决策

- 列表继续提升并读取 verdict、summary、身份、review 和时间字段；不恢复 displayed_score、
  failed_metric_names 或 `sort_by=displayed_score`。
- 新写入的 `record.body` 只保存 execution、metrics、trace snapshot、反馈和不可派生元数据；不保存
  顶层 verdict/summary，也不写 results、confidence、顶层 evidence、score、threshold、raw value 或
  passed。
- 详情读取优先按 `metrics + execution` 解析新 body；仅当二者都不存在且存在 `results` 时，临时按旧
  body 形状转换，不增加 schema version、v2 endpoint 或持久化迁移。
- 旧 `status=evaluated + passed=true/false` 分别转换为 good/bad Metric，`status=failed` 转换为 failed
  Metric；保留 name/reason/Metric evidence，丢弃 score、raw_value、threshold、passed 和 confidence。
  旧 bad result 未提供 reason 时使用旧 summary，再无 summary 时使用稳定兜底文案。
- 旧顶层 evidence 移入 execution.evidence。全部旧 results 都为 failed 时生成 failed Execution，code
  固定为 `legacy_execution_failed`，msg 优先使用旧 summary；只要存在 evaluated result，就生成
  succeeded Execution，保留单 Metric failed 事实但不推断整体执行失败。
- 转换后的详情仍只返回新版 `metrics/execution` HTTP 结构，verdict/summary 继续从转换结果派生，不
  校验旧 body 或提升属性中的持久化 verdict/summary 是否一致。对旧记录提交反馈时，新物理版本按新版
  body 写出，从而完成该逻辑记录的读时升级。
- `review_status` 直接表达完整反馈结果，除 `pending`、`confirmed`、`falsePositive` 和 `skipped` 外，
  独立支持 `cannotJudge`。反馈正文只保留可选字符串 `remark`，提交的其他反馈字段直接忽略。
- 该转换分支只覆盖一个月数据保留窗口。最早在 **2026-09-23**、并确认发布前旧 DiagnosisRecord 已从
  生产存储过期后，删除旧 body 转换、`legacy_execution_failed` 及对应测试；清理不改变新版 HTTP 或
  持久化合同。

### 保持不变

列表紧凑读模型、Job/时间/owner/实验组过滤、attempt 与反馈版本去重、物理查询上限、授权边界以及新版
详情字段保持不变。TaskStats 不从 DiagnosisRecord 推导，不受临时转换影响。

### 影响与风险

旧协议没有整体 Execution，转换只能从 MetricResult 形状推断；“全部 failed”可能把原本的多指标局部
失败近似为整体失败。旧 summary 在 succeeded Execution 下会按新版规则重新派生，可能与旧列表提升的
summary 不完全一致。临时分支若未按期清理会形成长期兼容负担，因此清理必须同时检查存储保留期和发布
时间，而不能只依赖日历日期。

### 验证

覆盖旧 evaluated good/bad、单指标 failed、全指标 failed、旧 evidence 迁移、新版详情输出以及转换后
重新写入只产生新版 body；反馈测试覆盖完整 `review_status` 枚举和 `cannotJudge + remark` 提交；现有
新版列表、详情、反馈和持久化测试继续通过。

### 实施说明

已实施：详情读取支持一个月旧 body 转换，新写入与 HTTP 响应仍只使用 metrics/execution。


## diagnosis-record-query-contract@20261012-2310

**Task 保留期后读取 Record 自身冻结定义**

```yaml
record_id: diagnosis-record/query-contract@20261012-2310
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-12T23:10+08:00
decided_at: 2026-10-12T23:10+08:00
implemented_at: 2026-10-12T23:10+08:00
supersedes:
  - diagnosis-record/query-contract@20260823-2329
related_records:
  - diagnosis-record/verdict-derivation@20260823-1933
  - metric-result/evidence@20260823-1933
  - executor/result-parsing@20260823-1933
affected_objects:
  - http-api
  - security
  - trace-retrieval
canonical_doc: docs/api.md
```

### 背景

新结果合同上线后，旧版 Trace 查询源在一个月保留期内仍会同时返回旧 `results` body 与新
`metrics/execution` body。列表读取提升属性及同一 Record body 的安全冻结定义，
响应仍为紧凑提升字段。因此旧记录仍可见；详情和反馈若只识别新 body，会在
用户点击旧记录时失败。HTTP 合同仍应只暴露新结构，不重新引入已删除字段或版本化端点。

### 决策

- 列表和详情附加 space_uuid 与经过允许列表投影的 diagnoser_definition。列表从同一物理 Record body 校验 uuid/job/task/diagnoser/version/target 身份后提取，body损坏或身份混杂仅无定义，不以当前catalog补齐。
- 只接受 Record snapshot schema3/score lightweight-v1，定义 UUID/version 必须匹配记录；校验有限字符串、category/role/weight/metrics 后投影公开字段，不依赖当前执行 ABI。列表不输出 Trace、Prompt、spec 或私密政策。
- Django 只在非Quick Task上下文明确过期时使用该记录定义，并复核Record Space、Job Space/Agent及个人授权；source标记record_snapshot。Quick receipt过期、权限拒绝与跨范围请求仍拒绝。无旧定义则保持unsupported_snapshot，不查询当前Diagnoser补齐。
- FE列表名称和详情metric标签均接受record_snapshot，历史数值、质量与反馈事实不重算。

- 列表继续提升并读取 verdict、summary、身份、review 和时间字段；不恢复 displayed_score、
  failed_metric_names 或 `sort_by=displayed_score`。
- 新写入的 `record.body` 只保存 execution、metrics、trace snapshot、反馈和不可派生元数据；不保存
  顶层 verdict/summary，也不写 results、confidence、顶层 evidence、score、threshold、raw value 或
  passed。
- 详情读取优先按 `metrics + execution` 解析新 body；仅当二者都不存在且存在 `results` 时，临时按旧
  body 形状转换，不增加 schema version、v2 endpoint 或持久化迁移。
- 旧 `status=evaluated + passed=true/false` 分别转换为 good/bad Metric，`status=failed` 转换为 failed
  Metric；保留 name/reason/Metric evidence，丢弃 score、raw_value、threshold、passed 和 confidence。
  旧 bad result 未提供 reason 时使用旧 summary，再无 summary 时使用稳定兜底文案。
- 旧顶层 evidence 移入 execution.evidence。全部旧 results 都为 failed 时生成 failed Execution，code
  固定为 `legacy_execution_failed`，msg 优先使用旧 summary；只要存在 evaluated result，就生成
  succeeded Execution，保留单 Metric failed 事实但不推断整体执行失败。
- 转换后的详情仍只返回新版 `metrics/execution` HTTP 结构，verdict/summary 继续从转换结果派生，不
  校验旧 body 或提升属性中的持久化 verdict/summary 是否一致。对旧记录提交反馈时，新物理版本按新版
  body 写出，从而完成该逻辑记录的读时升级。
- `review_status` 直接表达完整反馈结果，除 `pending`、`confirmed`、`falsePositive` 和 `skipped` 外，
  独立支持 `cannotJudge`。反馈正文只保留可选字符串 `remark`，提交的其他反馈字段直接忽略。
- 该转换分支只覆盖一个月数据保留窗口。最早在 **2026-09-23**、并确认发布前旧 DiagnosisRecord 已从
  生产存储过期后，删除旧 body 转换、`legacy_execution_failed` 及对应测试；清理不改变新版 HTTP 或
  持久化合同。

### 保持不变

列表紧凑响应、Job/时间/owner/实验组过滤、attempt 与反馈版本去重、物理查询上限、授权边界以及新版
详情字段保持不变。TaskStats 不从 DiagnosisRecord 推导，不受临时转换影响。

### 影响与风险

旧协议没有整体 Execution，转换只能从 MetricResult 形状推断；“全部 failed”可能把原本的多指标局部
失败近似为整体失败。旧 summary 在 succeeded Execution 下会按新版规则重新派生，可能与旧列表提升的
summary 不完全一致。临时分支若未按期清理会形成长期兼容负担，因此清理必须同时检查存储保留期和发布
时间，而不能只依赖日历日期。

### 验证

覆盖旧 evaluated good/bad、单指标 failed、全指标 failed、旧 evidence 迁移、新版详情输出以及转换后
重新写入只产生新版 body；反馈测试覆盖完整 `review_status` 枚举和 `cannotJudge + remark` 提交；现有
新版列表、详情、反馈和持久化测试继续通过。

### 实施说明

已按授权修复并增加离线传输、历史投影与作用域回归；不宣称真实模型或生产验证。

## diagnosis-record-query-contract@20261028-1518

**Read an exact Task batch without an implicit source-time window**

```yaml
record_id: diagnosis-record/query-contract@20261028-1518
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-28T15:18+08:00
decided_at: 2026-10-28T15:22+08:00
implemented_at: 2026-10-28T15:22+08:00
supersedes:
  - diagnosis-record/query-contract@20261012-2310
related_records:
  - diagnosis-record/verdict-derivation@20260823-1933
  - metric-result/evidence@20260823-1933
  - executor/result-parsing@20260823-1933
affected_objects:
  - http-api
  - security
  - trace-retrieval
canonical_doc: docs/api.md
```

### Background

Quick Check executes explicitly selected Trace IDs, including old Traces. A completed Task can have
persisted results whose source timestamps are outside both the global UI window and the record API's
implicit seven-day window. A link to that exact batch must not silently hide those results.

### Decision

- Record queries require one or more Job UUIDs. A nonempty `task_uuid` with both `start_time` and
  `end_time` omitted selects that batch without an implicit object-time or write-period predicate.
  Django forwards this request without injecting dates. The fixed-Task UI omits its global time filter.
- Other record queries retain the seven-day default start and current-time default end. Explicit
  endpoints are preserved, including requests supplying only one endpoint. The effective half-open
  interval filters `attributes.target_created_at`; its start also bounds the write `period`, whose
  upper bound remains now to include delayed writes and feedback. An empty Task ID does not opt out.
- List reads filter verdict before selecting the highest attempt and newest feedback version per
  logical record UUID. Lists read promoted fields and the same physical row's `record.body`, validate
  record identity before projecting its frozen definition, then sort and paginate within the existing
  50,000 physical-candidate cap. Lists expose compact fields, not Trace, Prompt, spec or private policy.
- Lists and details expose `space_uuid` and an allowlisted `diagnoser_definition`. A definition requires
  Record snapshot schema 3, score `lightweight-v1`, matching UUID/version, and bounded public fields.
  Invalid or mismatched bodies have no definition; current catalog data cannot replace it.
- Django may use a Record's valid frozen definition only after a non-Quick Task context has expired and
  Record Space, Job Space/Agent and user permissions are rechecked. It marks `definition_source` as
  `record_snapshot`. Quick receipt expiry, permission denial and cross-scope requests remain denied;
  missing frozen definitions remain unsupported. Frontend names and metric labels accept this source
  without recomputing historic values, quality or feedback.
- New bodies store `execution`, `metrics`, `trace_snapshot`, feedback and non-derived metadata.
  Verdict and summary are derived at write time for promoted fields and at detail read time from the
  body; detail reads do not validate those derived fields against promoted values. Removed score,
  threshold, raw value, passed, confidence and top-level evidence fields are not restored.
- The existing temporary legacy-body adapter remains: when `metrics` and `execution` are both absent,
  old `results` become good/bad/failed Metrics, preserving reasons and Metric evidence. An all-failed
  legacy result produces `legacy_execution_failed`; otherwise Execution succeeds, retaining local
  failed Metrics. Old top-level evidence becomes execution evidence. Feedback writes the new body.
  Remove this adapter only after the old production records have expired, not solely by calendar date.
- Feedback supports `pending`, `confirmed`, `falsePositive`, `skipped` and `cannotJudge`; its optional
  body field is `remark`. Owner filtering remains exact and optional, experiment tokens use OR
  semantics, and the existing model, category and keyword filters remain available.

### Unchanged

AgentLens does not authorize end users via Space/Agent query parameters; Django must enforce the
existing boundary before forwarding. TaskStats remains independent of record queries. Record writes,
retention, detail lookups, verdict derivation, frozen definitions and feedback semantics do not change.

### Impact and risks

Fixed-Task reads can now return retained results whose source or write timestamps are older than
seven days. They remain bounded by the authorized Job and exact Task and by the list candidate cap.
This does not promise recovery of expired records or more than 50,000 physical candidates. Removing
the implicit period predicate may change query plans; verify the supported OceanBase reader with real
old-source records. Rolling back restores the hidden-results problem without changing stored data.

### Validation

Cover fixed Task without dates, no/empty Task defaults, explicit one-sided and two-sided windows,
all three store query entry points, and proxy preservation of explicit endpoints. Retain existing
scope, deduplication, feedback and frozen-definition tests. Validate an older-than-seven-days Trace
through the real collector, diagnosis execution and both AgentLens and Django record reads.

### Implementation notes

Implemented under the current user authorization after review of this decision. The change is
limited to record time predicates, proxy defaults and the fixed-Task frontend request. No artificial
wide dates, collector limits, TaskStats semantics or source Trace data are changed. Targeted tests
first reproduced the implicit-window and overwritten-endpoint failures, then passed after the fix.
