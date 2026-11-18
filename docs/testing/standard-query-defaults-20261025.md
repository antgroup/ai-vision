# Standard native query defaults — 2026-10-25

This report validates the candidate's default native Trace/Session query paths on the same frozen data as [the preceding opt-in recovery report](session-query-recovery-20261025.md). Earlier slow observations and unsuccessful stages remain preserved. The current distributions are serial measurements of the new default, not a new paired comparison with the older build.

## Result and operating controls

Trace and Session acceleration, bounded list previews, batched Session summaries, verified STORED Token reads, automatic schema migration and continuous projection discovery are standard behavior. Only two emergency controls remain:

| Setting | Default | Recovery behavior |
| --- | --- | --- |
| `AIVISION_QUERY_MODE` | `auto` | `raw` bypasses projections for Trace/Session lists and facets while retaining filter semantics. |
| `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED` | `true` | `false` persists a shared fleet-wide suspension before untracked raw commits. A normal default-ON restart cannot clear it; explicit privileged recovery is required. |

The matching OceanBase provider owns managed initialization and its retry lifecycle. An internal production config marker prevents ingestion from running a second mutating initializer against the same provider/DSN/prefix; standalone ingestion keeps its zero-value bootstrap behavior. This adds no environment control. The backend entrypoint supervises the API and discovery worker. The worker enrolls historical data and refreshes dirty/context-stale summaries without separate per-Space processes. Docker image/source blueprints and Kubernetes configuration forward the same two controls; generated configuration is tested locally, while a live Kubernetes rollout is outside this report.

## Fixed data and measurement boundary

Both owned clones retain **109,858 raw Span rows and 36,612 canonical Trace/source rows**. The largest scope has 10,000 Sessions, 30,000 Traces and 90,000 Spans. Five synthetic profiles and the retained Eino, DeepAgents, DSH and Codex multi-turn traces use the original IDs, windows, request payloads and independent expected results. No new model was called or telemetry ingested for this replay. Full all-column raw fingerprints and original service preservation are checked after recovery; sanitized receipt hashes are included in [the evidence](evidence/standard-query-defaults-20261025.json).

The engine versions and Docker architecture/limits below come from read-back receipts. The linux/amd64 seekdb container runs under emulation on an ARM Mac; CE and the query fixture are linux/arm64. A zero Docker limit means no explicit container limit, not zero available resource. Versions, architecture and resources differ. Results are separated by engine and do not rank storage engines.

| Component | Reported version | OS / architecture | Docker memory limit | Docker CPU quota |
| --- | --- | --- | --- | --- |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0` | linux/amd64 | No explicit Docker limit | No explicit Docker quota |
| ce | `5.7.25-OceanBase_CE-v4.4.2.1` | linux/arm64 | 8 GiB | 4 |
| query-fixture | `Query transport fixture` | linux/arm64 | 2 GiB | No explicit Docker quota |

The HTTP path includes current-source Django product routes/authentication and the actual Go handler/parser/store over the clones. A fixed private test credential replaces full SigNoz authentication/authorization, while the actual main-API timeout middleware is loaded with its normal 60-second default and 600-second maximum. The external transport bypasses full service startup. The actual migration helper is called, but this is prepared local Mac/Docker evidence, not released-image, full SigNoz auth, cold/WAN, Linux-host deployment, Kubernetes, SLO or capacity proof. Runtime/binary/source hashes bind the measured processes to the tested code.

## Staged correctness and migration

- The product backend passed 164 targeted tests; the unchanged frontend passed 89 targeted regression tests and type checking. The three CLI deployment packages passed. SigNoz passed 158 top-level offline tests; 9 DSN-gated tests were skipped there, then 9 real SQL tests passed on seekdb and 9 on CE, with no skips in either SQL run. Both real API entrypoint packages compiled.
- Each engine passed 126 product API correctness reads over the five profiles and four frameworks. Auto/raw business hashes match for complete turns, matching IDs, counts, facets, usage, previews, ordering and full-content search. The six heavy raw 10k cases are deliberately outside that matrix; the three explicit raw reads per engine cover the 10k list and two Token thresholds.
- Four-framework Trace reads include baseline, SDK user, full input content, combined conditions and median Token threshold. Ready Trace Token predicates deliberately use complete clean-summary snapshot recovery with `unsupported_filter_field:total_tokens`, zero raw Traces and unchanged business results; Session Token predicates use verified STORED values in SQL. Sessions retain all window turns while predicates match the same Trace. Known zero is distinct from missing usage. The frozen older Session oracle receives only a copied query-mode label adapter; expected business values and data are unchanged.
- The first correctness runner incorrectly required every auto query to use an indexed page. It stopped after 94 successful reads when the Eino Trace Token case selected its supported clean-summary fallback. The failed harness receipt is retained. Only the external assertion was corrected to validate the exact fallback reason, zero raw scans and the same frozen oracle; production source bytes were unchanged. Both final correctness matrices were then rerun from the start.
- The earlier populated legacy-schema migration probes use an exact 36,612-row projection-table copy on each engine. The original CE attempt exposed a bulk DELETE exceeding the ordinary 10-second SQL limit after successful ALTERs. The final implementation commits cleanup in batches of at most 256 rows under the lease/generation fence, permits raw writers between chunks, preserves partial progress and respects a newer OFF. The original failure remains recorded.

| Engine | Existing projection rows | Historical migration helper | Healthy restart | Maximum observed DELETE chunk |
| --- | ---: | ---: | ---: | ---: |
| seekdb 1.4 (amd64 emulation) | 36,612 | 35.388 s | 11 ms | 232.778 ms |
| OceanBase CE 4.4.2.1 (arm64) | 36,612 | 36.266 s | 8 ms | 1025.012 ms |

These earlier projection-only migration probes passed with unchanged all-column raw data, unchanged global SQL deadlines, preserved main projection counts and independent probe-table cleanup. Their derived fixture populated the projection table, not the complete source/coverage state; they do **not** measure full three-table operator cleanup or the final Resume implementation. The final full-clone operator workflow below separately starts from 36,612 projections, 36,612 sources and all discovered coverage rows.

Normal initialization retains a two-minute budget. One explicit Resume **HTTP call** has a dedicated **four-minute server deadline and 245-second product client deadline**, below its five-minute maintenance lease. The management command then performs one normal bounded projection iteration when `--once` is supplied; its complete wall time can exceed the Resume HTTP budget. The four-minute/245-second limits are not a whole-command or full-rebuild deadline. The product sends the existing `timeout: 240` header for this action so the main-API middleware does not shorten it to 60 seconds. Four immediate context-deadline regression cases check ordinary requests at 30 seconds, Resume with this header at 240 seconds, and Resume without it at 60 seconds; no four-minute sleep is used. Ordinary GET/query budgets remain 30 seconds and global SQL remains 10 seconds. Healthy restarts preserve coverage and enrollment cursors. Neither the earlier migration observations nor the final command wall time establishes fresh deployment or complete backfill throughput.

A final source review found a P2 lifecycle ownership gap: after successful provider initialization, ingestion repeated the initializer. A temporary metadata error there could revoke eligibility after the provider's retry had already stopped, leaving acceleration unavailable until restart or explicit recovery. The internal managed-provider marker removes only that duplicate initialization. Three new offline SQL-mock tests cover managed ON/OFF no-SQL behavior and standalone healthy/error behavior; source-wiring checks bind the marker to the same selected OceanBase provider, DSN and prefix while retaining raw schema setup and emergency writer fencing. The reviewed patch/candidate bytes match the final Go validation receipt. This is offline/source verification plus the final source-bound query/helper replay, not a full-service startup or live metadata-failure injection claim.

## Current default performance

For each engine, six cells (1k/10k × list/Token ≥100/Token ≥0) contain one warmup and 20 formal observations: **126 requests per engine, zero errors**. Cases run serially; ordinary default workers remain running without pending projection work. P50/P95 use independent linear interpolation at `(n−1)×q`, recomputed from every retained sample and checked against the runner. The timer includes the product HTTP request, server work, full download and JSON parsing; login, oracle validation and receipt writes are excluded. n=20 is descriptive and does not define a production SLO.

| Engine | Sessions | Query | P50 ms | P95 ms | Matching Sessions |
| --- | --- | --- | ---: | ---: | ---: |
| seekdb 1.4 (amd64 emulation) | 1k | List | 167.9 | 188.0 | 1,000 |
| seekdb 1.4 (amd64 emulation) | 1k | Tokens ≥100 | 166.7 | 183.3 | 84 |
| seekdb 1.4 (amd64 emulation) | 1k | Tokens ≥0 | 173.4 | 184.6 | 909 |
| seekdb 1.4 (amd64 emulation) | 10k | List | 838.9 | 862.5 | 10,000 |
| seekdb 1.4 (amd64 emulation) | 10k | Tokens ≥100 | 792.4 | 808.9 | 839 |
| seekdb 1.4 (amd64 emulation) | 10k | Tokens ≥0 | 865.1 | 892.5 | 9,090 |
| OceanBase CE 4.4.2.1 (arm64) | 1k | List | 160.3 | 191.6 | 1,000 |
| OceanBase CE 4.4.2.1 (arm64) | 1k | Tokens ≥100 | 120.8 | 185.7 | 84 |
| OceanBase CE 4.4.2.1 (arm64) | 1k | Tokens ≥0 | 122.9 | 188.3 | 909 |
| OceanBase CE 4.4.2.1 (arm64) | 10k | List | 559.7 | 587.8 | 10,000 |
| OceanBase CE 4.4.2.1 (arm64) | 10k | Tokens ≥100 | 533.1 | 564.3 | 839 |
| OceanBase CE 4.4.2.1 (arm64) | 10k | Tokens ≥0 | 585.6 | 616.6 | 9,090 |

The preceding report's paired default-off candidate and baseline remain separately bound to their original source. Do not attribute cross-time/cache differences to this default-enablement change. The earlier adverse list/Token distributions and 1k regression are unchanged historical evidence, rather than silently replaced samples.

## Large-window recovery and memory

An indexed page that is dirty, unprojectable or crosses the requested boundary selects one complete repeatable-read snapshot, combining clean summaries with authorized raw spans. Explicit raw mode bypasses all derived summaries. The recovery budget is 50,000 Traces, 500,000 raw Spans, 512 MiB selected source fields, 16 MiB per Span and a 256 MiB compact response. Limits produce explicit errors, never truncated successful lists. Standard Session page bounds remain 50 Sessions, 5,000 selected Traces and 16 MiB; larger complete recovery uses the snapshot path.

After formal timing, only the two verified owned projector children are paused. Five derived sources per engine are marked dirty: one from the frozen 10k scope and one per framework; the Eino source additionally becomes temporarily unprojectable. Raw data and shared generation are unchanged.

The first large dirty-window replay exposed a **real API 400 failure**, distinct from the earlier harness assertion: the 30k-scope stored canonical rows total 269,699,890 bytes and their preview JSON adds 67,830,000 bytes (337,529,890 bytes before envelope/framing). With one dirty source, the attempted mostly-clean summary representation still exceeded the existing 256 MiB response cap, while the complete compact raw representation fit. The failed request and byte-inventory receipts remain preserved.

The final implementation preflights clean-summary bytes inside the same repeatable-read transaction. A typed representation-budget overflow discards that attempt and reads the full authorized raw window in the **same snapshot**, retaining the existing source, Span and response bounds. It does not increase caps, add a switch or alias/truncate the canonical protocol. Non-budget parse, permission, SQL and transport errors remain errors. Both engines' direct Go diagnostics confirm `recoveryReason=canonical_summary_budget`, zero projected Traces and all 30,000 raw Traces.

Each engine then passes the three 10k dirty-window queries through `raw_snapshot` and 40 focused four-framework Trace calls; the smaller framework auto windows still use mixed `hybrid_snapshot` with positive clean and raw counts. All match the unchanged frozen business oracle. Captured scope flags are restored, current revisions advance without rewinding, workers resume, and ready query results are checked again. Focused framework cases overlap the main matrix and are not claimed as extra independent data coverage. All final serving/correctness/performance receipts were rerun against the new binary; the earlier 510 pre-budget-fix, 596 pre-HTTP-status-fix and subsequent 596 pre-Resume-fix serving observations remain historical evidence and are not counted in final current-source validation. The earlier 18 OFF-reader checks and successful seekdb Resume are also preserved separately.

The following are **single correctness observations per stage and case**, not percentile distributions. The Stage column distinguishes an explicit `raw` reader from an `auto` reader recovering a dirty window; both legitimately return `raw_snapshot`. Each 10k response still returns 20 complete Session rows after examining the full window; list/Token100/Token0 totals are 10,000/839/9,090.

| Engine | Stage | Serving path | Query | HTTP seconds (n=1) | Matching Sessions | Clean / raw Traces |
| --- | --- | --- | --- | ---: | ---: | ---: |
| seekdb 1.4 (amd64 emulation) | Explicit raw | raw_snapshot | List | 16.923 | 10,000 | 0 / 30,000 |
| seekdb 1.4 (amd64 emulation) | Explicit raw | raw_snapshot | Tokens ≥100 | 16.133 | 839 | 0 / 30,000 |
| seekdb 1.4 (amd64 emulation) | Explicit raw | raw_snapshot | Tokens ≥0 | 16.154 | 9,090 | 0 / 30,000 |
| seekdb 1.4 (amd64 emulation) | Auto with dirty source | raw_snapshot | List | 17.030 | 10,000 | 0 / 30,000 |
| seekdb 1.4 (amd64 emulation) | Auto with dirty source | raw_snapshot | Tokens ≥100 | 16.017 | 839 | 0 / 30,000 |
| seekdb 1.4 (amd64 emulation) | Auto with dirty source | raw_snapshot | Tokens ≥0 | 15.848 | 9,090 | 0 / 30,000 |
| OceanBase CE 4.4.2.1 (arm64) | Explicit raw | raw_snapshot | List | 16.118 | 10,000 | 0 / 30,000 |
| OceanBase CE 4.4.2.1 (arm64) | Explicit raw | raw_snapshot | Tokens ≥100 | 15.729 | 839 | 0 / 30,000 |
| OceanBase CE 4.4.2.1 (arm64) | Explicit raw | raw_snapshot | Tokens ≥0 | 15.511 | 9,090 | 0 / 30,000 |
| OceanBase CE 4.4.2.1 (arm64) | Auto with dirty source | raw_snapshot | List | 16.608 | 10,000 | 0 / 30,000 |
| OceanBase CE 4.4.2.1 (arm64) | Auto with dirty source | raw_snapshot | Tokens ≥100 | 15.832 | 839 | 0 / 30,000 |
| OceanBase CE 4.4.2.1 (arm64) | Auto with dirty source | raw_snapshot | Tokens ≥0 | 15.348 | 9,090 | 0 / 30,000 |

During the final six sequential raw recovery calls, 103 periodic samples observed a maximum Go container usage of **1900.5 MiB** under its 2 GiB limit, and a largest Python process RSS of **1126.7 MiB**. Docker usage and RSS are different measurements; sampling can miss peaks. Recovery is substantially heavier than indexed serving. These observations do not establish concurrency capacity or a minimum deployment memory requirement. The earlier preflight memory observations are retained; workload/cache phase differences prevent a causal percentage-reduction claim. The implementation removes a duplicate whole-window encoding and enforces the response budget using the same renderer without retaining that extra copy.

## Emergency maintenance recovery

Concurrent maintenance also exposed a global-fence bottleneck in the earlier build: one already-complete Space's `limit=1` backfill request took 8.798 seconds while another Space held the fence; a separate 100-Trace/300-Span source snapshot took 43.7 ms. These are single diagnostic observations, not paired final performance results. The final implementation publishes under Read Committed while retaining generation/revision CAS, and commits backfill in at most 25-Trace chunks with an approximately two-second outer work budget checked between chunks. It releases the fence between chunks, rechecks coverage/generation on reentry, reuses the locked raw aggregate and answers completed scopes on the read-only fast path. Read-only snapshots remain Repeatable Read. The work budget does not guarantee a hard per-chunk lock duration. Both engines' real SQL tests exercise concurrent maintenance and completed-scope polling; the full rebuild below validates eventual completion on the frozen data.

The first operator-denial check found a separate protocol bug: a missing deployment key was correctly denied and state stayed protected, but the generic error adapter returned HTTP 400 (`invalid_input`) instead of HTTP 403. The narrow fix maps the existing forbidden business error to the correct HTTP status, with a direct handler regression. It changes no serving SQL, maintenance lifecycle or filter semantics. The original denial receipt is retained; the partially suspended owned clone was explicitly resumed with the actual management command, and full automatic coverage was restored before final measurements. All source-bound queries and distributions were rerun against the final binary rather than reusing earlier accepted requests.

A later full-state CE operator recovery exposed another real failure: the two-minute request deadline returned HTTP 504 after cleanup had committed partial progress. Read-back retained 2,308 projections, 36,612 sources, schema version 0 and all 109,858 raw rows. Retrying the earlier binary completed the remaining cleanup and reached ON, but that transition did not establish full projection readiness; the partial progress and retry profile remain recorded.

A subsequent intermediate-source rebuild still progressed too slowly on CE: the preserved 08:52 UTC observation had 9,550 projections and 9,612 sources, versus 36,612/36,612 on seekdb; both retained 109,858 raw rows and remained ON. Only the owned validation wait was stopped, leaving default workers running. This is an incomplete recovery observation, not an accepted rebuild or final-source result. It is retained separately from the final full rebuild below.

The final enrollment implementation batches the 25 source rows into one UPSERT with at most 200 bound parameters. At the maximum chunk size, source writes fall structurally from 50 statements to one, and total chunk SQL from 55 to six, excluding BEGIN/COMMIT. Existing source revisions advance once, new sources start at revision 1, dirty marking and coverage cursor commit atomically under the same fence, and the entire bounded page is validated before any source write. RC isolation, 25-Trace chunks and the two-second outer soft budget remain unchanged. Focused offline tests check batch rollback, invalid pages and OFF/generation changes; the final real SQL runs also check mixed existing/new revisions, physical summaries, cross-org isolation and stale-publication rejection. These SQL counts are not a measured speedup ratio, and a single fast aggregate page does not establish index health or a production SLO. The final full-clone rebuild below is required independently.

A following source version passed 153 offline tests and both nine-test SQL matrices, but its readiness check stopped at 26,385 projections and 26,462 sources on CE before any serving acceptance cohort began. Only that validator was stopped; default projectors kept running. At that stage, the publication API still used a separate transaction and maintenance-fence read for every Trace. This intermediate observation is preserved and excluded from the final query/rebuild acceptance.

Final publication uses one Read Committed transaction, maintenance-fence read and Commit for at most 25 items. Every per-Trace source lock, generation/physical-scope/revision CAS, projection write and dirty update is retained. Validation and duplicate rejection cover the entire HTTP batch before any transaction. Each chunk returns ordered successful receipts only after Commit; SQL or Commit failure returns no successful batch response and rolls back its uncommitted chunk, while earlier chunks may remain committed under the existing retry contract. The next chunk reacquires the fence and honors a newer OFF. Five new offline tests cover boundaries, mixed receipts, newer OFF, SQL rollback and Commit failure; both real SQL runs check a 26-item boundary with a stale last item and cross-org isolation. At 25 items, Commit operations and maintenance-fence reads each fall from 25 to one. These are structural counts, not a throughput multiple; the item bound is not a two-second lock-duration or ingestion-latency guarantee, and ordinary publication requests retain their 30-second deadline.

The final Resume path persists one recovery intent and reenters schema-version-0 cleanup under the same fenced generation/lease, preserving committed progress. A newer emergency OFF wins during repair. Only the explicit Resume HTTP call receives the four-minute/245-second budgets; normal initialization and ordinary SQL/query deadlines retain their existing limits. The final E2E checks exercise all three derived tables and require fully rebuilt, clean, scope-valid coverage plus the same frozen query results before acceptance.

On each full clone, the actual shared OFF helper advances the generation. Calling the default-ON initializer leaves OFF unchanged. The real resume handler rejects a missing deployment key with HTTP 403 and does not change state. This checks the handler key requirement under fixed fixture authentication; full SigNoz AdminAccess middleware is outside this transport.

While maintenance remains OFF, each engine also passes **nine AUTO-reader queries**: four framework Session baselines, four framework Trace baselines and the 10k-Session Token-zero case. All use `raw_snapshot` and match the final raw canonical hashes. These exercise automatic fallback from the normal reader, separately from the explicit `raw` setting.

The actual product `maintain_trace_read_model --resume-maintenance --once` command then discovers only its verified owned routes, uses the existing deployment-internal key in the subprocess environment and resumes exactly one data plane per engine. It invalidates rebuildable state, increments the generation and retains raw rows. That command performs one bounded projection iteration while the already running default supervised workers continue maintenance in parallel; together they complete all 36,612 summaries without manually configuring per-Space processes. Count catch-up is followed by zero dirty/invalid sources, complete coverage and **the same nine AUTO-reader queries per engine**, all back on verified `read_model` with identical canonical hashes. Original and clone all-column raw fingerprints remain equal. A normal ON setting alone cannot perform this fleet-wide recovery.

These are single wall-clock observations, with separate timing boundaries:

| Engine | Management command wall time, s | Later count catch-up poll wait, s | Final summary count |
| --- | ---: | ---: | ---: |
| seekdb 1.4 (amd64 emulation) | 32.767 | 0.154 | 36,612 |
| OceanBase CE 4.4.2.1 (arm64) | 327.868 | 1450.645 | 36,612 |

The CE observation includes a 327.9-second management command and a separate 1450.6-second later count-poll wait. Full recovery of 36,612 summaries on this prepared CE clone therefore remains a material operational cost, at roughly half-hour scale across these phases. This recovery workload is separate from the ready-list query latency table; it does not establish a recovery SLO or comparative database throughput.

The harness executes the two operator commands serially while the supervised workers run in parallel. A later seekdb poll with nearly zero wait can follow several minutes of CE command execution; it does not mean that 36,612 summaries were rebuilt instantly. The command includes the Resume HTTP call and its subsequent normal maintenance iteration, and its wall time may exceed 245 seconds. Endpoint cleanup was not timed separately. Polling begins after command execution and does not measure total time from cleanup to complete recovery; neither column is a pure cleanup duration, throughput result or causal cross-engine speedup. Final dirty/coverage/query and raw-integrity checks remain required independently.

Real SQL lifecycle tests additionally cover revision/generation ABA rejection, concurrent source changes, schema inspection/repair failure, missing derived structures, new OFF during recovery and deletion-aware invalidation. Real permission/transport/malformed-result failures remain errors. Functional rollback does not require removing the generated columns. Out-of-band raw SQL import, retention, restore or table replacement still requires explicit suspend and resume/rebuild; automatic detection of external mutations is not claimed.

## Delivery and remaining boundaries

Product PR #114, SigNoz PR #12 and documentation PR #20 describe the same default contract and two controls. English, Chinese and Japanese deployment/operator content is checked against the implementation. This iteration changes backend/deployment behavior; existing frontend screenshots retain their earlier source/runtime provenance and are not relabeled as fresh captures.

The [sanitized evidence](evidence/standard-query-defaults-20261025.json) includes all final formal/correctness/recovery samples, independently recomputed percentiles, request/body-result hashes, relative source hashes, migration and operator summaries, sampled-memory limits and final integrity receipt hashes. Full raw IO, private login/configuration, credentials and operational identities are excluded. Earlier reports and unsuccessful setup/migration receipts remain preserved.

No merge, release-image publication or deployed-mode change is established by these local checks. See [the operator reference](../deployment/en/trace-read-model.md) for controls, recovery order and resource bounds. Hosted CI results are reported separately on the exact PR heads.
