# Retained query-performance improvements: measured results and limits

Date: 2026-10-28. This is a review-PR summary of the retained implementation, not a release, supported-capacity SLA, or a claim that the current dashboard has passed all performance budgets. No new measurements were run for this summary.

## Selection and measurement scope

Results below select **complete fixed series** of the retained candidate. They do not assemble a best-of-each-class table from different runs. Original slower runs, changed request contracts and failed stages remain visible.

The workload consists of synthetic Eino-, DeepAgents-, DeepSeek Harness- and Codex-style Span graphs, with each style contributing 25% of Sessions and Traces. It is not a new four-SDK or real-model run. Only the database has the stated CPU quota; application CPU is uncapped, with one worker and four threads. Concurrency C5/C10/C20 means that many closed-loop HTTP clients, not that many users or a fixed-arrival-rate/database-TPS capacity.

Per-class P95 uses nearest rank with n >= 20; P99 requires n >= 100. Original budgets are 1000 ms core serial,2000 ms content serial,1000 ms compound serial and 3000 ms mixed per class. A pooled percentile does not replace a failed or unavailable class percentile. Complete source expectations were computed independently of responses, including page order, original input provenance and complete Session aggregates.

## OceanBase: retained-v3 fixed series, 4 database CPU

The dataset is 10,000 Sessions /30,000 Traces /180,000 Spans. The historical dashboard request is **seven fields, page size 200, full root input**. Its timing must not be assigned to the current eight-field request that also requires per-Span `inputEvidence`.

The formal functional gate passed 43/43. The primary performance matrix completed 3109/3109 correct requests without recorded HTTP/API errors or confirmed timeouts. It did **not** pass every original latency budget.

All seven formal core serial classes come from the same 140-request series, n=20 each, unit: ms:

| Query | P50 | P95 | Original 1000 ms budget |
|---|---:|---:|---|
| Numeric metrics | 742.817 | 1060.873 | Over budget |
| Historical 7-field dashboard, 200 rows/full root input | 512.912 | 729.618 | Pass |
| Trace metadata page | 313.188 | 399.910 | Pass |
| Session list | 727.115 | 926.272 | Pass |
| Session Token >= 100 | 725.303 | 1247.766 | Over budget |
| Selective Session Token threshold | 698.361 | 1153.958 | Over budget |
| Recent 200 operation previews | 596.189 | 703.817 | Pass |

| Complete fixed series | Correct /attempts | Per-class n | Highest class P95, ms | Original per-class result |
|---|---:|---:|---:|---|
| Content/compound serial, 22 classes |440/440|20|1485.484|All 20 content and 2 compound classes pass their respective budgets|
| Core C5, 7 classes |429/429|61–62|1339.124|7/7 <=3000 ms|
| Core C10, 7 classes |422/422|60–61|2348.177|7/7 <=3000 ms|
| All C5, 30 classes |604/604|20–21|1521.212|30/30 <=3000 ms|

In the sequential same-4-CPU comparison, the historical seven-field dashboard serial P95 changed from 2942.733 ms to 729.618 ms, and its core C5 P95 from 3212.316 ms to 860.869 ms. These are comparable request contracts, but the run order was not randomized. Other tails did not all improve: Session-list serial P95 changed from 702.387 ms to 926.272 ms, and selective-Token P95 from 660.021 ms to 1153.958 ms. Token >= 100 and the former Token >= 0 are different predicates and are not a same-query speedup comparison.

### Whole-series all-C10 variation, without cherry-picking

The fastest completed whole all-C10 series is shown with both other same-candidate series, not substituted for them. The three series retain the same 90 exact request variants. Each completed 609/609 correct requests, with 20–21 samples per class:

| All-C10 complete series | Aggregate P95, descriptive only, ms | Highest class P95, ms | Classes <=3000 ms |
|---|---:|---:|---:|
| Original formal series |2237.179|3839.766|25/30|
| Separate fresh A1 control, best whole-series observation |1564.133|1644.829|30/30|
| Separate return-to-candidate A2 control |1906.098|2746.196|30/30|

This supports a bounded optimization result, not a stable C10 SLA. The original slow all-C10 series and its five over-budget classes remain. Formal core C20 also remains over budget in all seven classes, with class P95 values 3028.273–3457.057 ms. The formal serial numeric/Token overages in the first table remain unchanged.

## SeekDB: current actual request, 2 database CPU

The physical inventory is 5000 Sessions /15,000 Traces /90,000 Spans. The 1k result is an exact time window over that same physical 5k inventory, selecting 1000/3000/18000; it is **not** a separate physical1k database. The 5k window covers the full inventory. Neither dataset bodies nor raw inputs were shortened to pass validation.

Every current dashboard performance request includes **eight fields, page size 200 and full per-Span original `inputEvidence`**. All successful current eight-field pages passed the frozen source gate: 63 at 1k and 24 at 5k. Source-validation singletons and warm samples remain separate; 87 successful pages are not pooled for a dashboard percentile.

Each serial column is its own complete 140-request series, all correct, n=20 per class. Units: ms; the core budget is 1000 ms:

| Query |1k-window P95|5k-window P95|
|---|---:|---:|
| Numeric metrics | 307.200 | 785.074 |
| Current 8-field dashboard, including inputEvidence | 1989.774 | 4874.357 |
| Trace metadata page | 157.812 | 229.445 |
| Session list | 538.826 | 574.742 |
| Session Token >= 100 | 491.732 | 569.532 |
| Selective Session Token threshold | 494.508 | 653.342 |
| Recent 200 operation previews | 582.664 | 231.163 |

The six ordinary core classes pass their serial budget at both windows; the current eight-field dashboard does not. Each window also completed a separate 440/440 content/compound series, 22 classes with n=20. The highest class P95 is 248.064 ms at 1k and 487.459 ms at 5k; all 20 content and 2 compound classes pass their respective budgets. These are retained ordinary-query results, not a cross-engine ranking against the different historical OB seven-field contract.

At 1k, core C5 completed 289/289 correctly, n=41–42 per class. Dashboard P95 is 3140.064 ms, exceeding 3000 ms; the other six classes pass. Per the frozen gate, all C5 and both C10 stages were not run.

At 5k, core C5 stopped after the first hard error:23/24 correct responses. The dashboard returned HTTP400/API3012 with downstream HTTP500 after 10018.081 ms. Original raw/status data were durably saved before validation. Bounded existing server logs record Error4012 at the 10,000,000 us SQL ceiling; duplicated logging does not create a second failed request. Client typed-timeout count remains0, while backend SQL-timeout evidence is 1. Each class has only 3–4 samples: **no class P95/P99 is reported**, and all later escalation stages were skipped. No timeout increase, query retry or sample filling was used.

Across the ten SeekDB phases, 1560/1561 attempts succeeded. The 86 legacy functional singletons,2 current eight-field/source-validation singletons, warm serial series and interrupted concurrent series remain separate. This combined count is not a performance distribution.

## Retained implementation

1. **cost-scope.** Dashboard metrics callers request `include_cost=false`; explicit cost-aware, detail and legacy requests retain their cost semantics. Avoid completeness-driven full-window cost hydration for top-level dashboard metrics.
2. **exact-agent.** Remove a redundant Agent attribute predicate only after exact Agent scope validation; retain authorization and scope checks. Keep supported requests eligible for the read-model path without weakening access control.
3. **metadata-only.** Use a metadata-only projection for metadata pages rather than hydrating large observation and operation JSON fields. Reduce LOB decoding and transfer when the response does not require full bodies.
4. **selected-operation-facts.** Read bounded facts and explicit previews for selected recent operations, with source-revision checks. Avoid reading every full Trace to display a recent-operation page.
5. **source-fragments.** Read complete source through bounded fragments: 2 MiB response limit and 128 MiB per-Trace limit; publish projections only after generation/revision/context checks and compare-and-swap. Handle large multi-Span source safely while rejecting stale or partial projection publication.
6. **content-completeness.** Automatically add and maintain content_complete_v1 incrementally; use its scalar completeness predicate where supported and retain the equivalent JSON fallback. Avoid repeated large-JSON completeness work while preserving content semantics and compatibility.
7. **safe-query-bounds.** Require a total-Token threshold of at least 100, duration at least 1 ms and counts at least 1, with finite safe upper bounds. Reject unhelpful or unsafe broad numeric filters. Token >= 100 is a changed predicate, not a same-query speedup over Token >= 0.
8. **existing-emergency-controls.** Preserve the existing two emergency controls, raw/capability/readiness fallbacks and authorization-error handling; add no deployment switch. Keep an operational escape route without introducing a fleet of experimental flags.

These changes preserve complete-source checks, authorization, projection revision/context/generation fences and functional fallbacks. They do not convert raw original input into a summary. Full-text/gram/forced-index experiments and the rolled-back v4 empty-result pruning are **excluded** from the proposed changes.

## Remaining boundaries

- The current OB eight-field dashboard has a correct first source-validated observation of 8154.492 ms and only two saved warm observations of 7724.236/7607.076 ms. The historical seven-field paired controls were 526.074/387.742 ms. Neither variant has n=20; the old recorder lost the fifth raw/status/timing, so its outcome remains unknown. Internal-space Error4184 is a bounded candidate, not exact attribution for that missing response or all observed latency.
- Current eight-field dashboard performance, the 5k SeekDB C5 SQL failure and the retained OB original-budget failures are open limits. Source correctness is not latency acceptance. A summary-first page followed by original evidence on visible/selected/expanded items is a future implementation/retest proposal, not an already measured fix.
- Source was prepared from declared synthetic data. Offline preparation, successful queries and migration read-backs do not establish online-ingest reliability, arbitrary SDK coverage, production/VM/cold-cache behavior, or frontend paint time.
- The performance experiment ended with the explicit old safe baseline restored: old application assets for OB, default database CPU quota, original volumes/fences/limits preserved, and SeekDB stopped. No new default-CPU performance result is implied.

## Local source validation for delivery

The final retained source was checked again on 2026-10-28. [Commands, results and reviewed source hashes](evidence/native-query-validation-20261028.json) are recorded separately from load measurements:

- Product backend: 195 targeted tests passed with `manage.py test`; these fixtures do not establish live database or ingestion behavior.
- Product frontend: 436 tests passed across i18n, framework metrics/lifecycle, Trace evidence/filters, source system and dashboard metrics. Typecheck and production build passed. The existing large-chunk build warning remains.
- Local Playwright protocol fixtures: all 12 cases in `nativePrimaryMetrics.spec.ts`, `nativeEnvironmentFilter.spec.ts` and `sessionCostSummary.spec.ts` passed. The first setup attempt inherited an HTTP proxy and executed no cases; retry removed that proxy only for the loopback test process. These fixtures do not establish storage performance.
- SigNoz: `go test -p 1 ./pkg/ingest ./pkg/modules/aivision ./pkg/telemetrystore/oceanbasetelemetrystore ./pkg/apiserver/signozapiserver -count=1` passed. The real-DSN integration environment was unset for this source-suite run; the engine/API evidence above is separate.
- No production FULLTEXT index, vector search, v4 empty-result pruning or new deployment switch is included.

The [query contract](../../ai-vision/docs/traces/query-performance-contract.md) records the cost compatibility rules, source bounds, automatic additive migration, capability fallback and retained emergency controls. The original ingest packet limit remains distinct from the new source-page transport; a single oversized Span is not declared fixed by this patch.

## Actual browser evidence

The built-in browser captured the same prepared 1k time window on physical 5k SeekDB data. Only the frontend changed sequentially: main `historical-source-revision-redacted` before, retained frontend after, with the same candidate backend/data plane. This is a UI compatibility comparison, **not** a complete historical-deployment or performance A/B.

| View | Before | After |
|---|---|---|
| Framework dashboard | [Primary cost card](evidence/native-query-ui-20261028/before-dashboard.jpg) | [Usage and timing without the primary cost card](evidence/native-query-ui-20261028/after-dashboard.jpg) |
| Trace list | [Embedded primary cost summary](evidence/native-query-ui-20261028/before-trace-list.jpg) | [Usage and execution information](evidence/native-query-ui-20261028/after-trace-list.jpg) |
| Session list | [Embedded primary cost summary](evidence/native-query-ui-20261028/before-session-list.jpg) | [Usage, Trace count and timing](evidence/native-query-ui-20261028/after-session-list.jpg) |

The [network summary](evidence/native-query-ui-20261028/ui-network-summary.json) records the real `include_cost=false` metrics request and the current eight-field dashboard body, including `inputEvidence`. The actual list still requests complete input evidence; the UI change does not claim to solve its remaining latency.

Restoring [Token >= 0](evidence/native-query-ui-20261028/after-session-token-invalid-0.jpg) leaves the input at `0`, marks it invalid and sends no Session-list request. Correcting it to [Token >= 100](evidence/native-query-ui-20261028/after-session-token-valid-100.jpg) updates the URL and actual request to `100`, returning 900 matching synthetic Sessions. The [interaction receipt](evidence/native-query-ui-20261028/ui-interactions.json) records those assertions. The [390 px capture](evidence/native-query-ui-20261028/after-session-narrow-390.jpg) retains the table's own horizontal scroll; header scope controls still extend beyond that narrow viewport, so this is not a claim of complete mobile-layout acceptance.

The [capture manifest](evidence/native-query-ui-20261028/ui-evidence-manifest.json) pins image hashes, build identities, scope and privacy inspection. Images contain synthetic records; account details are outside the captures. Details remain the cost-inspection route; no new model execution, hosted release or page-latency result is implied.

## Evidence pointers

[source summary](evidence/native-query-performance-20261028.json) provides the complete selected per-class rows, whole-series identities, retained-change source filenames and SHA-256 values. The pointers below are filenames and content hashes; no personal path, test identity, credential or private customer payload is included in this public summary. Raw evidence remains in its original sealed archive.

| Source filename | SHA-256 | Purpose |
|---|---|---|
| `report-v3.zh-CN.md` | `ad00d47269b54c8f0ad250193aea5d4af2e3f7881b50b5b57e618ee5f42ce575` | Historical retained-v3 context |
| `ob-acceptance-audit.json` | `d4fbbf466a9a5efc6671d5bc7baa75c8de5c40fee710edee3198f92ec7078e5c` | Whole-series OB counts and original budgets |
| `ob-v3-class-budget.csv` | `187708c38abb5324b9b02b494d6cc695a754399431fad786c1008cd8d9e2de8e` | All OB per-class measurements, including failures and controls |
| `report-acceptance-seekdb.zh-CN.md` | `398823ca1ddec81667d42352b20e0fa56c842d9cdf83a477cb09d217bd156ecd` | Current actual-request limits and final safety restoration |
| `seekdb-current-query-comparison.csv` | `07f803a6691efaf28dc94bfd26d801bc89a3273246154c3747565e9fe837d7e1` | Current SeekDB per-class measurements |
| `seekdb-execution-index-r1.json` | `d0f27db435b67b1cd72eb420d63fba201b6e86936d0dd1d722cb3c974e103a82` | All ten SeekDB phases, failures and skipped escalation stages |
| `final-acceptance-review.md` | `b2343d11d8ed295e3b3315d8f5bba24616ab5f9d4d57f01941545fe8c43a18e1` | Independent current-request and restoration review |
| `review-source-r1/manifest.json` | `37bdd23ce0ea2281ed910f0aa6a280736e166f4c331dfd3ca508f7f5e72c4894` | Reviewed product and data-plane source package |
| `workload_v3.py` | `8bec18ebf5a7058ba6ed63d383c79112ad99c5cc4b2cc32914fb64853f8a2fdf` | Synthetic four-style producer |
| `report-acceptance-seekdb-sources.json` | `1515c6bece1e6a1d898b7ac8955e93a646e637c22754415734b124a3308748ee` | Sealed underlying evidence index |
