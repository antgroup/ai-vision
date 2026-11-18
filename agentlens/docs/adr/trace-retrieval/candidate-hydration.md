---
object_key: trace-retrieval
topic: candidate-hydration
title: Trace 候选补拉
---

# Trace 候选补拉

## trace-retrieval-candidate-hydration@20260823-0234

**按 pre-evaluate 候选并集补拉完整 Trace**

```yaml
record_id: trace-retrieval/candidate-hydration@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - dataset
  - executor
  - task-runner
canonical_doc: docs/diagnoser/README.md
```

### 背景

多数目标只需少量 span 字段即可结束诊断，直接为全部目标读取完整 Trace 会放大查询和投影成本。

### 决策

- 旧版 Trace 查询源的第一阶段一次读取目标身份、Selector 字段和所有 Executor 声明字段的并集。
- 每个 Executor 只接收自己声明的 exact span 行和字段；待完整评估的 target 按所有 Diagnoser 取并集。
- 完整 Trace 对候选目标只查询、清洗和投影一次，再按 target 与 Diagnoser 路由。
- phase1 合并查询失败直接使 Task 失败，不降级为仅查询 target key。

### 保持不变

离线数仓每行已经携带完整 spans，可直接处理。

### 影响与风险

字段声明和候选判定可显著降低读取量；错误声明可能造成最终 FAILED 或额外补拉。

### 验证

由 TraceStore phase1、字段合并、Runner hydration 和多 Diagnoser 共享测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## trace-retrieval-candidate-hydration@20261028-1728

**Limit diagnosis candidates by distinct Trace count before reading lightweight fields**

```yaml
record_id: trace-retrieval/candidate-hydration@20261028-1728
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-28T17:28+08:00
decided_at: 2026-10-28T17:28+08:00
implemented_at: 2026-10-28T17:36+08:00
supersedes:
  - trace-retrieval/candidate-hydration@20260823-0234
affected_objects:
  - dataset
  - executor
  - task-runner
canonical_doc: docs/diagnoser/README.md
```

### Background

The diagnosis form exposes a maximum Trace count, but phase1 previously applied SQL `LIMIT`
to Span rows before deduplicating Trace IDs. One Trace with many Spans could consume the
whole budget and truncate the evidence available to Selectors and pre-evaluation.

### Decision

- For query-based SigNoz datasets, phase1 first selects distinct, nonempty Trace IDs, ordered
  by Trace ID ascending, with `query.limit` applied to those IDs. This is deterministic
  selection for an unchanged source, rather than a newest-first or random sample.
- A second query reads target identity, Selector fields, and the union of all Executor
  field declarations for the selected IDs. It has no Span-row limit. Both queries share
  the same rendered time bounds, tenant predicates, Dataset `where`, and existing exact
  Span-name conditions. No matching IDs means no second query.
- Each Selector receives only identity and its requested fields. Each Executor receives
  only its declared exact Span rows and fields. Full Trace hydration uses the union of
  targets requiring complete evaluation across Diagnosers; retrieval, cleaning, and
  projection are shared once per target.
- Failure of either phase1 query fails the Task; there is no fallback to target keys alone.

### Unchanged

Fixed explicit Trace selection bypasses query-based sampling. Query windows, scope ownership,
Selector placement before `source.total`, pre-evaluation decisions, full Trace context windows,
and result persistence retain their existing contracts. Existing Dataset payloads require no
migration, and historical Task results are not rewritten.

### Impact and risks

Datasets can now select up to the configured number of Traces even when each contains many
Spans. The extra database round trip and reading all matching lightweight rows can increase
cost. Source data may change between the two reads; this does not promise a transactional
snapshot. Rolling back restores Span-row limiting and may reduce diagnosis coverage again.

### Alternatives

A single query with a nested distinct-ID subquery could reduce round trips but complicates
column rendering and query plans. Increasing the Span-row limit cannot guarantee a Trace
budget or complete pre-evaluation evidence.

### Validation

Regression coverage includes distinct-ID limits with uneven Span counts, complete selected
lightweight rows, deterministic ordering, empty results, preserved time/scope/name predicates,
escaped IDs, failure propagation, and unchanged explicit selection. The related suite passed
354 tests; 34 tests requiring a disposable control database were skipped. Read-only OceanBase
validation selected 10 distinct Traces and all 39 matching rows from a window containing
36 Traces; the old query selected one Trace. Repeated reads matched. No external judge was called.

### Implementation notes

The current user approved distinct Trace selection followed by field retrieval in this session.
Implemented in `TraceStore.read_trace_targets`; the in-memory query adapter supports distinct
Trace IDs and ordering. SQL behavior is also exercised against SQLite with both column dialects.
The existing date-normalization patch remains independent and unchanged.
