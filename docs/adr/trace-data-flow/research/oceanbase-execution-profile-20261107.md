# OceanBase Execution Profile for Layered Trace Capacity

Research date: 2026-11-07, Asia/Shanghai. This is an execution-design input to the [proposed ADR](../layered-capacity-isolation.md), not a qualified database feature matrix, migration or performance result. No SQL, database change, VM operation or load test was performed. Official documentation and owning source were inspected.

## Selected foundation

Use the existing OceanBase/seekdb MySQL-compatible telemetry store to hold accepted normalized Span values, a narrow current catalog/index and revision/quota facts. Short bounded transactions keep those facts atomic. Scope-leading indexes serve interactive discovery and exact selected-content access; qualified columnar/hybrid representations can serve analytics. This uses database capabilities rather than requiring another raw object store, replay service or analytical database for the first read delivery.

It does not imply unlimited LOB reads, strict serializability, automatic per-item failure isolation, or bounded browser work. Those remain explicit application contracts. LangSmith's newer SmithDB route also means the competing products should not be described as one uniform ClickHouse implementation; their separation of representations remains useful independent of their storage choices. [SmithDB architecture](https://docs.langchain.com/langsmith/self-host-smithdb).

## Actual repository baseline

The standard Compose file defaults to a floating CE image, while the seekdb Compose file and private delivery channel use distinct image digests. There is no one global minimum CE/seekdb version established by those files. Historical qualification records cover CE4.4.2.1 and seekdb1.4.0.0 for their fixed source/runtime combination; they do not qualify this new catalog.

Owning source anchors:

- `deploy/docker/compose.yaml:207`; `deploy/docker/compose.seekdb.yaml:211`; `scripts/ci/release_assets.py:16-18`.
- Paired SigNoz `pkg/ingest/schema.go:57-62`: raw primary key `(org_id,space_id,trace_id,span_id)` and existing scope/time indexes.
- `pkg/ingest/read_model.go:12-32,53-73,87-105`: ordered source locks, source/projection keys and source revision maintenance.
- `pkg/modules/aivision/read_model.go:456-461,638-714`: read-only repeatable-read, source/generation locks and revision-CAS publication.
- `pkg/modules/aivision/read_model.go:179-196`: protocol capability is separate from database health/schema readiness.
- `pkg/ingest/trace_statistics.go:22-47,74-95` and `trace_projection_summary.go:66-72`: engine eligibility and actual stored-column definition checks. The statistics threshold is not a product-wide minimum database version.

These are reusable mechanisms, not an existing independent catalog or a claim of deployed capability. The external source baseline and patch fingerprint are recorded in the [current implementation assessment](../reviews/langfuse-langsmith-gap-20261107.md).

## Capability profiles

| Profile | Use | Qualification / non-goal |
|---|---|---|
| Transactional rowstore baseline | Same-tenant telemetry raw/current catalog/index/revision facts, unique keys, exact point locks/CAS, bounded transactions and typed scalar range indexes | Shared baseline for supported CE/seekdb combinations. Check the actual dialect/schema/driver; no Oracle package or Enterprise-only dependency. |
| Native catalog snapshot | Prefer qualified MySQL `AS OF SNAPSHOT` for J1; omit the application catalog-version journal and global publication epoch in this mode | Both OB and seekdb have official flashback documentation. Qualify exact readable snapshot acquisition, undo retention, DDL behavior, privileges, compaction and cost before selection. |
| Analytics storage | Rowstore for current directory, topology and point reads; selectively add columnstore/hybrid storage to bounded analytical facts | Exact edition/version/BP and storage configuration matter. Columnar replication, write amplification and optimizer choice require measurements. Not a mandatory whole-payload copy. |
| Database workload governance | Separate connection pools/users and session limits; where available, map ingestion/interactive/projection/export to resource groups | CE/cluster configuration, cgroup, privileges and actual enforcement must be checked. seekdb does not inherit distributed multi-tenant/read-replica guarantees. |
| Search and materialized views | Optional bounded-text full-text/vector indexes and rebuildable aggregation projections | Separate feature semantics/freshness. They do not replace exact literal predicates, canonical root selection, ownership or original evidence. |
| Native LOB range | Optional replacement for application chunks after proof of bounded engine/driver work | Output-size limits alone do not prove bounded I/O, allocation or decode cost. MySQL SQL functions must be assessed, not Oracle `DBMS_LOB` examples. |

A feature descriptor records product, edition, exact version/build/BP, compatibility mode, schema/index fingerprint, storage format, relevant settings/privileges and the qualified serving profile. Protocol support, engine eligibility and actual per-Space readiness are independent facts. Request handlers perform no DDL; a brand/version string alone is insufficient.

## Transactions, ownership and index layout

The existing raw key and source fence are a stronger starting point than rebuilding consistency in another pipeline. Before a bounded write chunk, validate outside the transaction, ensure the uniquely keyed Trace coordination row exists, and acquire its exact point lock in the established lock order. Compute replacements from current accepted values, then atomically update raw, current catalog/index, revision, reversible byte/count contribution and any advertised outcome facts. Optional rich projection stays asynchronous and revision-fenced. Reuse the durable dirty-source/revision mechanism; do not add a second raw-payload queue by default.

The transaction must use the same telemetry database/tenant and connection. This is not a promise of atomicity with Django's control-plane database, another OB tenant, an external Langfuse project or object storage. External Space/Agent UUIDs and authenticated SigNoz org are authorization identities, not assumed Django composite primary keys. The type-level `agent_product` is not the configured data-source filter.

OB's documented default is Read Committed; its snapshot-based `SERIALIZABLE` is not a blanket strict-serializability guarantee. Explicit locks/conditional updates protect invariants. An empty `FOR UPDATE` result cannot replace creating the unique coordination row. A future hard scope-wide quota needs its own atomic reservation and contention design; per-Trace locks alone cannot enforce a global total. [OB isolation](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001974669), [seekdb MVCC and row locks](https://github.com/oceanbase/seekdb-doc/blob/V1.0.0/en-US/200.develop/300.read-and-write-data/200.read-data/300.use-operators-and-functions-in-query/1000.lock-query-results-select-for-update.md).

Preserve the existing last-successful-arrival policy and implement this ADR's current-value no-op target; the inspected ingest path still advances the source revision on replay. Unique Span IDs and upsert syntax do not define causal ordering or prove an ambiguous commit failed. A1 receipts may resolve a known persisted outcome only when the receipt itself is part of the committed contract; do not silently introduce permanent request deduplication that changes later corrected updates.

Recommended logical access keys, mapped to the existing physical scope:

- Current catalog: equality prefix for authenticated org/Space and proved Agent ownership, followed by the selected canonical time and Trace-ID tie-break. Additional supported filters need their own qualified access path.
- Topology: scoped Trace plus stable time/Span ID, and a separate scoped Trace/parent/Span-ID branch index.
- Selected content: exact scoped Trace/Span/component/version key. Do not read full Trace payloads to establish a selected field's scope.

Cover only bounded scalar fields needed by high-frequency reads. Large JSON and LOB values do not belong in every covering index. An index can avoid primary-table lookup, but has write/storage costs. `LIMIT` is a result bound, not a scan/sort/lock-work proof; OB even documents that a limited `FOR UPDATE` can lock the selected set before limiting. [Covering indexes](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001974740), [SELECT locking restrictions](https://docs.oceanbase.com/oceanbase-database/V4.4.2-select-mysql-compatible-mode).

Distributed partitioning/locality is a later scale profile: seek locality by scoped Trace where appropriate, but measure the fan-out of catalog time indexes and cross-partition transactions. Time partitions must preserve stable-ID uniqueness, corrected upserts and late-arrival routing. Do not repartition historical raw tables or introduce a scope-global serialization row as an incidental first migration.

## Native snapshots before a custom history journal

Official MySQL-mode flashback syntax exists for both products. OB's tenant-level `undo_retention` retains historical versions; seekdb's guide describes the same record-level feature. A native profile can let each page use a short query at one fixed database snapshot, rather than keeping a UI transaction open or writing duplicate catalog-history rows. [OB flashback](https://docs.oceanbase.com/oceanbase-database/V4.4.2-data-protection-flashback-query), [seekdb flashback](https://github.com/oceanbase/seekdb-doc/blob/V1.0.0/en-US/400.guides/500.manage/200.high-availability/600.flashback-query.md).

Qualification must establish a minimally privileged way to obtain an already-readable committed database point; an application wall clock or guessed SCN is not that proof. Every table in a historical query uses the same point. Current authorization is rechecked separately: historical ownership is not permission to preserve revoked access. Tokens bind profile, query, scope, position, schema generation, database snapshot and original expiry.

A10-minute cursor is not justified by setting retention to exactly600 seconds. Retention needs headroom for request execution, expiry and operational margin; historical reads can fail across incompatible DDL. Test GC/compaction, schema change and retention reduction and return explicit expired/unavailable outcomes instead of mixing live results into old pages. Undo retention can retain history for a larger tenant workload, including large changing values, so its storage cost may exceed a narrow catalog journal. Select native history only when those costs and the retention contract qualify; use the application journal profile otherwise. No live token can silently change profile. [OB undo retention](https://www.oceanbase.com/docs/common-oceanbase-database-cn-1000000002015615).

## HTAP, LOBs and resource governance

OceanBase supports row, column and hybrid storage, but edition/version/BP, schema and plans must be checked. Keep interactive tables narrow and row-oriented; evaluate column/hybrid storage for compact metrics/operation facts under concurrent writes. Same-engine transactions do not make asynchronous materialized views instantly current. Store explicit source revision/watermark and availability/freshness for any derived view. Canonical root status, unknown usage and ownership remain application semantics, not arbitrary SQL `MAX`/`SUM` substitutions. [Storage design](https://en.oceanbase.com/docs/common-best-practices-10000000002431625), [CE4.3.5 release notes](https://www.oceanbase.com/en/docs/common-oceanbase-general-1000000001780339).

OUTROW LOB storage uses native auxiliary storage/locators; it is not evidence that a selected SQL substring avoids full-value processing. Persist component byte lengths during accepted writes and avoid selecting LOBs for navigation. For legacy content, qualify size/range functions and driver behavior; otherwise use explicit local refusal or bounded application chunks. Logical column limits, SQL packet limits, request limits and Trace totals are different units. Do not advertise a large SQL type as the online read budget. [LOB guidance](https://en.oceanbase.com/docs/common-best-practices-10000000002046821).

Database governance complements application admission. Separate the four workload pools, set/read back session deadlines and parallel limits, and test cancellation/permit lifetime. On capable OB deployments, qualify actual resource-group CPU/I/O/memory behavior with the host/container setup. Space is not automatically an OB tenant; placing each Space in another database tenant is not required and changes atomicity/routing. seekdb uses its own profile and application/process controls where native governance is unavailable. Workarea percentages and parallel limits are not hard caps on all memory or all concurrent requests. [Resource groups](https://en.oceanbase.com/docs/common-ocp-10000000002169136), [seekdb architecture releases](https://github.com/oceanbase/seekdb/releases).

## Required evidence before claiming an advantage

1. Dual-connection atomicity/replay/ambiguous-outcome tests: raw, index, catalog and counters agree; rejected updates preserve old data; healthy different Traces commit; hot-Trace lock waits and retries are bounded.
2. EXPLAIN plus actual execution evidence for small pages and selected fields: index ranges, primary lookups, scanned/returned rows, partition fan-out, no full-window core fetch or payload hydration. Plans alone are insufficient.
3. LOB experiments at growing content sizes and start/middle/end ranges: exact bytes/hash, server/driver/process memory, I/O, cold/hot latency and cancellation. Compare with chunks before enabling native-range claims.
4. Native snapshot experiments on exact CE/seekdb packages: valid point acquisition, cross-table/page consistency, late writes/deletes, GC, compaction, DDL and expiry; measure retained-history cost against the journal alternative.
5. Row/column/hybrid and governance experiments under concurrent ingest, interactive reads, projection and export: correctness, write amplification, ingest lag, interactive tail latency, CPU/I/O, spill and actual reclamation. No unmeasured throughput/latency benefit is promised.
