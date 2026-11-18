# Dashboard query performance verification — 2026-10-26

The dashboard bypassed the accelerated Agent Trace/Session paths. The fix removes redundant observation filters, uses canonical server-paginated Sessions, reuses compact aggregation facts and reads contents only for the latest selected operations. The largest remaining cost is complete-window statistics.

Follow-up: the [OceanBase aggregation report](ob-ap-dashboard-aggregation-20261026.md) records the subsequent typed-statistics and bounded-event implementation, including actual CE 10k measurements. The results and remaining limits below describe the preceding compact-window stage, not that final implementation.

## Measurement boundary

- Product baseline: `historical-source-revision-redacted`; SigNoz baseline: `d6d5d94cdbfc70885f7218254464d9043ea6f326`. Candidate source is an uncommitted, hashed snapshot in the two task branches.
- Prepared local Django API, real SigNoz handlers/store and real seekdb / OceanBase CE SQL. SQLite control metadata was copied; SigNoz organization authentication used a fixed private test claim. This does not establish released-image, cold deployment, full SigNoz authentication, VM or Kubernetes performance.
- Four previously captured framework datasets: Eino, DeepAgents, DSH and Codex. Synthetic expansion: 1,000 Sessions / 3,000 Traces on both engines; 10,000 Sessions / 30,000 Traces on seekdb. Optimized CE 10k was not measured.
- Both final variants use direct loopback with explicit `NO_PROXY`. Earlier system-proxy runs, invalid adapter runs and intermediate global-context experiments are excluded. The earlier 40-second observation must not be mixed with these direct-loopback results.
- Baseline is one observed request per case; candidate ranges are three requests. These are prepared-environment observations, not statistical confidence intervals, percentiles or a production SLO. Timed queries ran without projection writers. Preparation cloned task-only derived tables and explicitly populated facts; those costs are separate.

## Main results

| Engine / window | Request | Baseline, n=1 | Candidate, n=3 range | Storage HTTP calls |
|---|---|---:|---:|---:|
| seekdb / 1k Sessions — Session list | dashboard_sessions | 29.431 s | 0.121–0.143 s | 6015 → 1 |
| ce / 1k Sessions — Session list | dashboard_sessions | 26.135 s | 0.106–0.184 s | 6015 → 1 |
| seekdb / 10k Sessions — Trace list, 200 | dashboard_traces | 16.360 s | 0.367–0.548 s | 1 → 1 |
| seekdb / 10k Sessions — Session list, 10 | dashboard_sessions | API 3027 (scan limit) | 0.538–0.602 s | 1 → 1 |
| seekdb / 10k Sessions — statistics | dashboard_metrics | 17.172 s | 7.601–7.801 s | 1 → 1 |

The old Session request fetched 200 rows before local display pagination; the repaired dashboard fetches the visible server page (10/20/50). Total Session counts and selected-window turns are retained. This is a user-visible first-page comparison, not identical legacy/canonical wire formats.

## Root causes and corrections

1. The Trace request repeated `agent_uuid` as an observation attribute predicate, making the canonical index reject the query. It now retains the authorized instance scope without that redundant predicate.
2. Dashboard Sessions omitted `canonical-trace-v1`: the 1k case made 6,015 list/detail/count requests. Server pagination and drawer semantics now match the Agent Trace page.
3. Statistics forced raw Spans. Versioned private observation facts preserve parents, missing usage, decimal costs, status aliases and model/time grouping. `required_supplement` fills absent facts without changing the existing projection context or making ready lists ineligible.
4. Full summaries still carried IO: the 30k window was about 381.9 MB, exceeding the 256 MiB representation budget and reverting to about 170.9 MB of raw Spans. The new `metrics-window` streams a 29-field representation, approximately 86.4 MB for this dataset, retaining transaction, source, count and byte checks. Content predicates keep complete content.
5. Statistics, event stream and trends requested the same window concurrently. Only active reads with identical endpoint, credential hash and complete physical scope/window are coalesced; completed reads are removed immediately. The final three-request concurrency run made one metrics-window request plus two bounded content batches, returned correct results, and took 10.23 s.
6. Event contents are hydrated only for selected latest operations, with source revision, parent, time and authorization checks. Revision drift discards the hydrated batch and retries a complete raw snapshot.
7. Large Session page selectors now use numeric jump/previous/next controls. Small or nonnumeric option sets retain the existing selector. Same-scope pending pages preserve metadata and show loading rather than presenting old rows as the new page.

## Verification and recovery

- 286 final candidate API reads: zero API errors; 63 complete business/oracle comparisons passed. Metadata timing/path and the Trace row diagnostic sourceRevision are excluded from business hashes. Canonical Session pages 10 and 20 also match independent frozen per-session manifests.
- Product backend: 167 relevant tests passed. Go: 122 top-level affected-package tests passed, with offline SQL skips reported separately; fresh independent seekdb and CE integration runs passed. Frontend/build/browser evidence is recorded in the UI receipt.
- Native auto/raw and 1k raw regressions passed. Final direct-loopback 10k forced-raw statistics also passed at 19.21 s with the same business hash. Earlier proxy/time-out diagnostics remain preserved, not silently retried into the formal matrix.
- On both SQL engines, removing one task-only metrics supplement left Trace/Session lists in read_model. Statistics correctly used raw; one production project_batch publication restored compact statistics. Canonical fields, source revision, dirty flag and context were unchanged.
- Existing query-mode and maintenance emergency controls remain the only controls. Missing new endpoint/capability selects complete raw recovery. Authentication, transport, malformed data and wrong-scope responses remain errors.

## Remaining limits

- Statistics over 30k Traces still take 7.6–7.8 s serially, and the three-section request group about 10.2 s. The data plane still reads full stored row_json before trimming. Materializing compact columns is a possible next step, but it requires a separate additive migration that preserves ordinary list readiness and binds compact data to source revision/context.
- The initial Agent Session page-20 control was 0.557 s versus 0.722–0.802 s candidate. A subsequent interleaved n=3 control found baseline 0.521–0.824 s (median 0.580 s) and candidate 0.569–0.715 s (median 0.589 s), overlapping ranges with identical business hashes and one indexed request on each side. The earlier difference did not reproduce as a sustained material regression. This remains a small local sample, not statistical equivalence or a universal speedup claim.
- No source commit, push, PR, product image publication or platform deployment was performed during this measurement phase. Test-derived SQL namespaces and immutable receipts remain available for reproduction.

## Evidence

Local receipt directory: `outputs/dashboard-performance-20261026/`. Key files: `final-summary.json`, `final-performance-summary.json`, `final-correctness-comparison.json`, `supplement-upgrade-seekdb.json`, `supplement-upgrade-ce.json`, `final-runtime-source-binding.json`, `metrics/backend-final.log`, `signoz/metrics-window-review.md`, and the final UI/cleanup receipts. Experimental context normalization is explicitly marked as preparation and is not presented as automatic production migration.

## Final browser and preservation read-back

Final frontend checks: framework 36, Session IO 32, source selection 4, localization 10; production build/typecheck, targeted lint/format checks passed. Final production screenshots are ui/10 through ui/16. The 10k synthetic FrameworkDashboard mapping is explicitly isolated from the real DSH dashboard. The final visible five-request page returned all 200 responses, approximately 7.8 seconds for the slowest section; Session first page 0.879 s, last page 0.579 s, size-50 reset 0.885 s. Numeric jump kept focus during loading, took 23.4 ms to two frames, and produced no observed main-thread long task above 50 ms.

All nine protected container identities/configurations/mounts/start times/restart counters and both engines' original 109,858-Span fingerprints match the initial receipts. Seven task Django listeners and two task transport containers were stopped; task-derived tables/containers and private recovery receipts were retained, not dropped. In-flight sharing is process-local; this run does not establish multi-replica latency.
