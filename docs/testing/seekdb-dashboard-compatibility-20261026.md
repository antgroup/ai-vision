# Lightweight seekdb dashboard compatibility — 2026-10-26

The seekdb compatibility path reuses typed statistics and bounded recent-operation reads without changing the OceanBase query or deployment default. OceanBase CE remains the recommended default for teams and growing observability workloads; seekdb is the lightweight choice for individual use and local development.

On the same prepared 10k-Session seekdb data, statistics median fell from **7.721 to 2.057 seconds** and five concurrent dashboard APIs completed in **3.426 seconds**, compared with **8.060 seconds** for the compact route, a **57.49%** reduction. All **93 API responses** compared across compact, typed, raw and concurrent reads have identical business bodies. These are local API observations, not browser render time or a production SLO.

## What changed

- Recognized complete seekdb 1.x release versions from 1.4 can use the typed schema. Older, unknown, mixed/unknown-suffix and future 2.x versions retain complete-window recovery. No user-facing switch was added.
- seekdb uses ordinary row-table DDL. The actual 1.4.0.0 instance rejected the existing explicit `WITH COLUMN GROUP(all columns)` suffix with SQL 1064. `TraceStatisticsDDLForVersion` selects the appropriate DDL using initialization's already-read version; the original OceanBase DDL remains byte-identical. It does not catch SQL errors and retry a different dialect.
- seekdb also rejected `GROUPING SETS`. Its aggregate expands one authorized narrow relation across five constant grouping axes and uses `CASE` / `GROUP BY`. Raw operation kind remains available for billable-usage predicates; grouping-axis flags distinguish omitted keys from actual NULLs. The existing accumulator and public contract are reused. OceanBase's original grouping-sets builder remains byte-identical.
- Incremental publication, generated receipts, all-Agent completeness checks, exact usage/cost/latency semantics and source-revision/maintenance-generation checks remain in place. Recent events select at most 200 keys before bounded source hydration. The two existing emergency controls remain authoritative.

## Measurement boundary and preparation

The immutable consumer is product `48abd55f4eda73a75dd57edb6f163e543e157e5f`. Control Go source is `bf1d85382a77917be1ed313a094e04092a78eb18`, whose seekdb engine gate selects the compact recovery route; the candidate is a hashed seven-file worktree patch on that base. Both use the same current stored seekdb rows, physical scope, fixed window and direct loopback with `NO_PROXY`. This is a route comparison, not an exact pre-feature storage comparison or a cross-engine comparison.

The retained database reports `5.7.25-OceanBase seekdb-v1.4.0.0`. Its engine settings are **2 CPU, 2 GiB memory, 2 GiB datafile and 2 GiB log disk**. The database image is Linux/amd64, running on an aarch64 OrbStack Docker VM with 12 CPUs and 12,599,844,864 bytes of memory. The database container has no Docker CPU/memory hard quota. The configuration therefore does not establish an isolated full-stack capacity result, native ARM seekdb support or a standalone Linux/amd64 deployment result.

The fixture reuses frozen Eino, DeepAgents, DSH and Codex captures: **12 native Traces**, plus **10,000 synthetic Sessions / 30,000 Traces / 90,000 Spans**, yielding **30,012 projections / 60,028 typed operations**. No models were called again.

Before preparation, both engines' original raw fingerprints and all 30,012 projection scalars / compact metrics matched. Canonical base rows were copied locally within seekdb; equivalent narrow facts were copied from the retained CE experiment. A server-side version/receipt marker prepared synthetic projection eligibility. Full canonical-column hashes with that deliberate marker removed, full typed-fact hashes and all receipt bindings matched. The canonical JSON before additions was 312,202,478 bytes; bootstrap stored JSON was 316,256,979 bytes before full native publication. The **61.55-second bootstrap is test preparation**, not production historical backfill throughput.

Separately, all 12 native snapshots were rebuilt with the actual product builder and published through the real Go batch handler. Full statistics envelopes were read back, receipts matched, sources remained clean, and the subsequent projector iteration saw zero further work. This distinguishes the actual publisher proof from bulk synthetic materialization.

## Same-seekdb results

The three individual API cases each used interleaved control/candidate observations, **n=3 per route**. All first observations and ranges are retained; these are not tail percentiles or statistical confidence intervals.

| Individual request | Compact median | Typed median | Typed range, n=3 |
|---|---:|---:|---:|
| Statistics | 7.721 s | 2.057 s | 1.212–3.700 s |
| Operation trend | 6.933 s | 3.053 s | 2.997–3.619 s |
| Latest event stream | 8.158 s | 0.727 s | 0.626–1.026 s |

Five actual APIs were then replayed concurrently over independent connections using one existing login session. Control/candidate rounds were interleaved, n=3 each:

| Concurrent request | Compact median | Typed median |
|---|---:|---:|
| Statistics | 7.939 s | 3.405 s |
| Operation trend | 7.627 s | 3.423 s |
| Latest event stream | 8.056 s | 0.875 s |
| Trace list, 200 | 0.523 s | 0.642 s |
| Session list, 10 | 0.789 s | 0.929 s |
| All five completed | **8.060 s** | **3.426 s** |

The all-request ranges were **7.660–9.646 s** and **3.124–4.221 s**. Trace and Session list medians increased by **119 ms** and **140 ms** while sharing the database with aggregation. The individual statistics range also shows substantial variability; the experiment does not establish stable two-second latency for every window.

Summed concurrent database-to-Go read bytes decreased from median **322,687,537 to 4,621,446**; Go-to-Python responses decreased from **89,273,742 to 3,197,859**. These are protocol/HTTP transport counters, not physical disk reads or browser payloads. Statistics alone read **318,961,886 → 432,410 database bytes**. Latest events use one operation-page request and two bounded batch-snapshot requests instead of a complete compact window.

Production Go-driver EXPLAIN inspection shows one authorized raw/p/s/t/c input branch, followed by a Cartesian join to five materialized constant axes. It does not contain five copied authorization joins. This is not a claim of three total physical scans: completeness, latency and partitions are separate data statements, with schema/maintenance checks and internal storage accesses. Earlier SQL-profile timings remain diagnostic and are not mixed into the endpoint samples.

## Write cost and recovery

The same current publisher and identical full 12-Trace / 28-operation payload were measured against two task schemas: one without optional statistics tables/receipt column, one with them. Each had one excluded warmup, followed by three interleaved observations; timing covers publisher plus database after decode/Ping and excludes HTTP, startup and preparation.

| Small publication batch | Median | Range, n=3 |
|---|---:|---:|
| Base schema only | 44.292 ms | 31.600–50.007 ms |
| Typed statistics schema | 115.433 ms | 112.670–400.239 ms |

The added asynchronous projection cost is **71.141 ms per measured batch**, about **+160.62%**. The 400 ms observation is retained. This is repeated small-batch publication, not raw-ingestion throughput, sustained capacity or a historical catch-up benchmark. OceanBase's earlier write-cost percentage must not be substituted for this seekdb result.

All **28 functional/edge checks** returned complete expected business bodies: missing facts and incremental repair; unchanged ready Trace/Session pages; dirty selected/unselected Agents; maintenance OFF; raw mode; partial-Trace boundaries; numeric dimensions and precision bounds; limits 1/2/200; empty selection; old unsupported endpoint; revision drift and same-revision/new-generation ABA. State was restored after each mutation. Permission, transport and malformed-success errors remain visible errors.

Exact row DDL, STORED receipt read-back and old-writer invalidation passed on seekdb. The real fresh optional initializer took **1.489 seconds** over 12 native Traces, with a **22.154 ms** idempotent retry; partial-DDL retry preserved existing page-body hashes, maintenance state and released leases. This does not estimate migration time on a large populated production table. Adding the generated column requires normal DDL permissions and can consume resources or wait for locks.

Final offline checks cover three Go packages: **198 top-level tests passed / 497 pass events including subtests**, with **nine real-SQL tests skipped without a DSN**. Real SQL/API checks described above are separate executions. Independent source review resolved two findings: overly broad version suffix admission and the unsupported seekdb column-group DDL. Reviewed, tested and runtime source hashes match. No frontend/Python runtime code changed in this adaptation; the consumer remained the frozen commit.

## Preservation and limits

Final full hashes of both typed tables match preparation. Each original SQL engine retains **109,858 raw Spans / 36,612 source and projection rows**, clean state and identical fingerprints; original nine services, engine parameters and global timeouts are unchanged. Only task Django/Go listeners were stopped. Proof tables, failed-attempt artifacts and task containers remain available.

No release image, production rollout, cold installation, browser rendering, Kubernetes, multi-replica or sustained-load validation was performed. The default recommendation remains OceanBase, with seekdb offering a measured compatibility path for personal use. No additional knob, forced index, parallelism setting or data-engine migration was added.

The [portable evidence](evidence/seekdb-dashboard-compatibility-20261026.json) contains source/log hashes, full timing samples and validation summaries without credentials or message bodies. Local details remain in `outputs/seekdb-ap-light-20261026/`, including `final-summary.json`, `final-crosscheck.json`, `stage-a.json`, the preserved failed DDL attempt, `native-product-publication.json`, `sql-profile.json`, `write-comparison.json`, `source-binding-final.json`, `integrity-before.json`, `integrity-after.json`, and `cleanup.json`. Compare OceanBase results separately in the [earlier OB report](ob-ap-dashboard-aggregation-20261026.md).
