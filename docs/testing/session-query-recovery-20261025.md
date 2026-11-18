# Session query recovery — 2026-10-25

This report supplements [the initial release review](trace-session-query-validation.md). The earlier adverse timings and 1k-list regression remain part of the record. The work here diagnoses and changes the repeated Session query work on isolated copies; it does not assign a unique cause to the unchanged binary's cross-time slowdown.

## Scope and query changes

A read-only per-statement timer around the actual Go method found 48 explicit SELECT statements for a nonempty 20-session page: two coverage checks, two counts, one page query, forty per-session summary size/fetch queries, and three facets. On the retained seekdb runtime, a single 10k-list method observation spent 1,055.7 ms in the forty summary queries out of 1,857.7 ms total. Token ≥100 and ≥0 method observations took 5,468.5 / 4,913.3 ms; the five repeated JSON Token groups dominated those requests. These are method/client-wall diagnostics, not HTTP percentiles or database CPU telemetry.

The candidate makes two independent, default-off changes in the companion SigNoz PR:

- Page batching checks and retrieves selected summaries in two queries. It retains complete turns, exact binary Session IDs, match IDs, order, counts and facets in one read-only repeatable-read snapshot.
- Two versioned STORED generated columns derive the original Token CASE directly from `row_json`. Token count/page/facet queries read those values after checking the full generated expressions and types. Older publishers updating `row_json` update the derived values automatically.

No startup hook or read request migrates storage. The explicit forward/rollback SQL changes only the projection table. Missing or mismatched optional columns retain the previous JSON strategy; inspection/permission/database errors remain errors. Zero remains a real known-usage threshold. Whole-page batch preflight may invoke the existing payload-limit fallback earlier near the boundary; 50-session, 5,000-Trace and final 16 MiB limits remain.

## Fixed inputs and environment

The five synthetic scopes and four retained framework sessions use the previous IDs, payloads, windows, filters and independent expected results. There are 109,858 raw Span rows and 36,612 canonical Trace/source rows across six scopes. CE cloned all four tables; the seekdb sandbox cloned the three derived tables needed by the Session method. The original tables, services and feature modes were retained.

The existing seekdb 1.4 container is linux/amd64 under emulation on an ARM Mac. The new OceanBase CE 4.4.2.1 container is native linux/arm64 with a 4-CPU quota and 8 GiB container limit (6 GiB configured observer memory). Architecture, version, configuration and resource allocations differ: the two engines must be reported separately, with within-engine paired comparisons. This is not a pure database-engine ranking.

The HTTP driver runs current-source Django product routes, response validation and the actual Go domain handler/parser/store. Five owned SQLite snapshots carry independent endpoint bindings and explicitly disable application OTEL. An external test-only Go transport substitutes fixed private authentication for the SigNoz application middleware and bypasses service startup hooks. This measures the query path, not full SigNoz authentication, production deployment, cold start, WAN or capacity. No model was called and no new telemetry was ingested for this replay.

## Stage receipts

- Original raw data was re-fingerprinted with deterministic all-column `mysql -B` reads; all six hashes matched the historical archive. Both clone paths additionally checked every original column and schema/index definitions.
- Two engines each passed a 31-case scratch matrix for generated-column CREATE, INSERT, old-writer-style UPDATE and ALTER over existing rows. Both preserve the original SELECT CASE behavior. The existing path already rounds some extremely large JSON numbers and saturates out-of-range DECIMAL casts; this optimization does not fix that boundary. Tested integers such as `2^53 + 1` remain exact.
- Before migration, four full product API reads proved missing-column fallback and semantic parity on both engines.
- Explicit migration took about 8.9s on seekdb and 10.2s on CE. All 36,612 rows on each engine matched both original expressions after migration. Only each maintenance connection received a longer DDL budget; global SQL and API budgets stayed unchanged.
- Each engine passed 80 current-source API correctness reads: pilot, 1k, 10k, long, large IO, Eino, DeepAgents, DSH and Codex. The baseline/candidate business hashes match, including complete turns, matching-turn IDs, totals, facets, usage, previews and order.

## Paired performance

Both engines completed **252 requests each: 12 warmups and 240 formal observations**, with zero transport, API, timeout or oracle failures. There are three cases, two scales and two variants; each formal cell has n=20. Pair order and case order are randomized. Linear interpolation at `(n-1)*q` defines P50/P95. HTTP timing includes the product request, server work, full download and JSON parsing; authentication, oracles and report writes are outside the timer. Formal values are paired within the same engine and fixed schema; the optional columns are present for both variants.

| Engine | Sessions | Query | Baseline P50 / P95 ms | Candidate P50 / P95 ms | P95 reduction |
| --- | --- | --- | --- | --- | --- |
| seekdb 1.4 (amd64 emulation) | 1k | List | 160.5 / 173.8 | 138.8 / 144.2 | 17.1% |
| seekdb 1.4 (amd64 emulation) | 1k | Tokens ≥100 | 501.2 / 532.8 | 131.3 / 141.2 | 73.5% |
| seekdb 1.4 (amd64 emulation) | 1k | Tokens ≥0 | 494.1 / 567.5 | 139.8 / 148.0 | 73.9% |
| seekdb 1.4 (amd64 emulation) | 10k | List | 649.3 / 711.2 | 574.5 / 606.7 | 14.7% |
| seekdb 1.4 (amd64 emulation) | 10k | Tokens ≥100 | 3,964.7 / 4,146.1 | 501.1 / 535.4 | 87.1% |
| seekdb 1.4 (amd64 emulation) | 10k | Tokens ≥0 | 4,073.5 / 4,251.4 | 591.3 / 639.2 | 85.0% |
| OceanBase CE 4.4.2.1 (arm64) | 1k | List | 145.7 / 156.8 | 119.1 / 134.3 | 14.3% |
| OceanBase CE 4.4.2.1 (arm64) | 1k | Tokens ≥100 | 454.1 / 522.6 | 115.9 / 149.3 | 71.4% |
| OceanBase CE 4.4.2.1 (arm64) | 1k | Tokens ≥0 | 467.8 / 535.4 | 120.1 / 159.7 | 70.2% |
| OceanBase CE 4.4.2.1 (arm64) | 10k | List | 968.9 / 1,077.4 | 695.2 / 791.4 | 26.5% |
| OceanBase CE 4.4.2.1 (arm64) | 10k | Tokens ≥100 | 5,240.1 / 5,412.4 | 641.9 / 729.1 | 86.5% |
| OceanBase CE 4.4.2.1 (arm64) | 10k | Tokens ≥0 | 5,234.8 / 5,332.0 | 682.7 / 718.3 | 86.5% |

The warmed paired baseline also improved substantially relative to earlier observations. For example, the initial batch-only 10k list observation was 5,051.7 → 948.3ms (n=1), while the formal within-engine comparison is 711.2 → 606.7ms on seekdb. Only the latter supports the approximately 14.7% list P95 reduction. At 10k, Token P95 improves by roughly 85–87% on seekdb and 86.5% on CE. Do not attribute all cross-time warming/state changes to these query changes. The engine results also have different architecture/resource allocations and are not an engine ranking.

The formal matrix covers list and two Token thresholds at 1k/10k only. Content combinations, long sessions, large IO and the retained framework cases are correctness observations, not new percentile distributions. The earlier complete-session/Trace frontend and backend checks remain separately bound to their original revisions; this backend-only follow-up does not rerun UI or model generation.

The existing 2026-10-25 adverse test remains visible: 10k P95 list 5,256.1ms, Token ≥100 9,489.7ms, Token ≥0 9,332.1ms; the earlier 1k ordinary-list regression was 171.77 → 446.67ms. Those were a retained runtime and another time window. They are neither paired with this candidate nor silently relabeled as current-source evidence.

## Concurrency, rollback and final checks

Four independent clients ran 20 mixed 10k candidate requests on each engine (five per case: list, Token ≥100, Token ≥0, content+metadata). All 40 completed with identical frozen-oracle results and zero errors. Observed maxima were 1072.1ms on seekdb and 1828.2ms on CE. These small probes are separate from the serial distributions and do not define capacity or a production SLO.

The four-package Go check passed 87 top-level tests; six DSN-gated tests were skipped in that offline run and then separately executed successfully on **each** engine. The first seekdb integration run had four passes and two setup failures because this Session-only sandbox lacked the empty raw-table template used by existing `CREATE TABLE LIKE` fixtures. An empty template was added only to the owned sandbox after timing, and all six integration tests were rerun. The original failure log remains retained; no formal sample was replaced.

Storage rollback was executed on both clones with the owned Go driver stopped. Both optional columns were removed, all 36,612 projection rows remained, and four post-restart product API reads returned the same business hashes through the old JSON path. The fixture deliberately kept its opt-in request path for this missing-column test; ordinary operator removal first disables stored reads on every worker. Original services were not stopped or restarted.

The final all-column raw fingerprints match all six pre-test hashes: **109,858 original rows unchanged**. Sanitized full formal samples, per-stage receipts, independent percentile recomputation, source hashes and rollback results are in [the recovery evidence](evidence/session-query-recovery-20261025.json). Inputs and unsuccessful setup receipts remain preserved in the local task workspace.

## Rollback and acceptance boundary

The page-batch and generated-read switches are independent and default off. Turning either off restores its previous query path; no data is deleted. The Python legacy/shadow/prefer controls remain available. Disabling generated reads does not undo the stored columns' write/storage effect: disable and verify every query worker first, then use the separate maintenance rollback to drop only the two optional columns. Raw Spans, row JSON and source tracking remain.

After verification, the five owned Django fixture processes and the two new test containers were stopped. Both clone datasets/volumes remain available, with the optional columns rolled back. Read-back verified that original SigNoz/seekdb configurations, mounts, start times and restart counts, plus all three original Python PIDs, stayed unchanged.

The PRs remain unmerged and no default mode or deployed image is promoted by this report. See [the operator guide](../deployment/en/trace-read-model.md) for phased activation and functional rollback.
