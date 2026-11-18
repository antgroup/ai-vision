# Dashboard module loading — 2026-10-28

This phase reduces eager work and duplicate presentation without changing backend, storage, collection, or full Trace-detail contracts. It extends the retained query work in [the native-query report](native-query-performance-20261028.md). Refs #124; no #127 work or issue closure is included.

## Change and tested identities

Implementation: `6f50a4818468077ccae8bef9429af91c7917f515`; baseline: `cd938f04d08cc09911e2a28d34a9e2863cc35428` (the earlier Framework prompt-removal commit). The baseline already removed its 200-Trace/eight-field original-input aggregation. This change takes the remaining default **business query definitions from four to two**: numeric metrics and the most recent 200 operations. Authentication, configuration and metadata HTTP requests are additional.

- Sessions start closed and query only while open. Closing cancels the exact active client request; closed refreshes stay idle. The complete Session-list link remains available without opening the table.
- Token trend reuses metrics. Activity trend runs while selected; its failure is independent of primary modules.
- Four primary metrics replace nine equal-weight cards. Supporting statistics, partial usage, unknowns, reported zeroes and Chat/Embedding distinctions remain visible.
- Recent model/tool rows share one type-filtered table, retaining status, units, usage and precise Trace/Span links. Duplicate model/tool tables and dashboard raw JSON are removed.
- Common/DSH and Codex prompt aggregations are also removed. Claude Code/CodeFuse dashboards are not otherwise redesigned. Full originals remain in Trace/Span details.

Prepared Mac Docker: **30,000 synthetic Traces / 180,000 Spans / 10,000 Sessions**, four framework styles, no new real-model execution. Fixed bounds: `1792598400000` to `1793203199000` ms. OceanBase database NanoCPUs stayed `4e9`, applications and data volumes were retained. Current `cpu.max` is `1600000 400000` (ratio 4); earlier raw periods were not recorded, so period equality is not claimed.

Frontend index: old `94e2370142c52590dc82c98b914164943d5483306213a4b300b348387a2b1319`; new `66aeef03712bc09cd0af1e98364202118d3ebb507b1d008ff6279d5b4816c33d`. Backend tree remained `c14124f461ee2323d8daea49e90cc75496cc4f84a8dbac137c378c0132e6c011`, and the SigNoz 991 build/live binary remained `43e27f2d772ea18fbc98f0d9e3c8123532edfe2566e8439c96dd5ae76faf05eb`. Only the frontend was switched; backend/Go/database/app-database processes were retained.

## Verification

Full frontend lint, typecheck, production build and `npm test` passed; the Node test reporters counted **1,261 passed, zero failed/skipped**, and the additional custom test scripts exited successfully. The existing large-bundle warning remains. The reviewed lifecycle fixtures use real React/React Query and the existing section hook with controlled transport; they cover first open, close, refresh, reopen, errors, in-flight cancellation, six identity changes, and late response rejection. The retained-source/usage tests passed. The updated Playwright protocol case was not executed in this phase; actual browser acceptance below used the Codex built-in browser.

The staged harness exposed and retained a missing-label recording error, an unscoped detail404, an ISO-time parser error and an overly specific CPU-period assertion. These were corrected in test preparation, with original expected data/raw receipts preserved. They are not counted as successful runtime samples or presented as product defects.

## API serial control

Old/new phases used the **same four frozen wire requests**, with 20 round-robin serial observations per class, no retry or sample replacement. Independent producer-derived checks covered counts, usage/null/coverage, model groups/hour buckets, Session IDs/order/usage/previews, 200 event IDs/order/usage/source previews, and activity buckets. All **160/160** planned requests were HTTP/API/canonical-correct. P95 is nearest rank, the 19th of 20. A rise greater than both 20% and 100 ms required investigation; no class crossed it.

| Query | Old P50 / P95 ms | New P50 / P95 ms |
| --- | ---: | ---: |
| metrics | 928.87 / 1969.29 | 611.15 / 657.95 |
| sessions | 1740.72 / 2797.41 | 438.54 / 470.17 |
| events | 721.49 / 784.82 | 512.82 / 573.07 |
| events-timeseries | 2436.36 / 3758.30 | 710.64 / 742.58 |

Backend code and request bodies did not change. Natural cache warming and temporal variation remained; **these lower API timings are not attributed to the frontend and are not browser-ready times**. Metrics/event payload sizes remained 12,029 / 199,317 bytes; small Session/trend serialization-size variation is retained in the receipt.

## Actual browser module behavior

Three authenticated reloads per version used the same absolute window and natural browser/server cache state. All business requests completed HTTP 200 and the rendered 30k / 200-operation data were verified. Time is from the document request until the last business response completes, followed by a DOM check; it is not a paint metric or P95.

| Observation | Old | New |
| --- | --- | --- |
| Default business calls | 4 each | 2 each |
| All default business responses complete | 1,657 / 1,438 / 1,396ms | 984 / 927 / 934ms |
| Activity first open | Eager in old default | One query, HTTP 744 ms; action-to-verified-DOM 890 ms |
| Sessions first open | Eager in old default | One query, HTTP 542 ms; action-to-verified-DOM 757 ms |
| Session next page | — | One query, page 2, HTTP 532 ms |
| Close Session / return to Token trend | — | Zero new business calls |
| Refresh with both optional modules closed | — | Metrics + recent operations only; HTTP 517 / 445 ms |

Optional first opens still require waiting; deferred requests are not claimed to disappear. Same-scope reopening may reuse valid data or refetch stale data. Client abort does not prove that an already running database query immediately stops.

The source-row link opened the exact synthetic Span, retained Agent and bounds, and displayed a six-node Trace with the matching input/output markers. Separate private-data checks compared the declared detail fields of all 120 Spans in the 54 / 66-Span large Traces with retained originals, including untruncated attributes/input/output/resources and normalized metadata. These samples had empty events/links; this is not coverage of nonempty event/link payloads or byte identity of redundant physical JSON. The fixed private window remains 50 Traces/1,062 Spans; physical Space inventory is 54 / 1,066 because four records were added outside that window. Original 50 data and source/projection fences remain identical.

## Window-switch peak and actual rollback

One initial new-page empty-window→full-window observation took 3,049 ms for metrics and 2,371 ms for operations (4,096ms action-to-verified-DOM). This remains in the evidence. Logs locate 2,959 / 2,300 ms in the existing typed aggregate/operation routes, with no raw/window fallback or engine error in that interval. Background maintenance also slowed, but the specific queue/cache cause is not established.

To avoid hiding that peak, the old frontend was actually restored, the same interaction was run three times, and the new frontend was restored and tested again. Backend/database processes and data were retained.

| Complete-window restore, 3 controlled observations | Old frontend | New frontend |
| --- | --- | --- |
| Metrics HTTP ms | 2,315 / 1,751 / 1,787 | 1,385 / 1,348 / 1,370 |
| Recent operations HTTP ms | 1,215 / 842 / 851 | 362 / 402 / 473 |
| Action-to-verified-DOM ms | 3,008 / 2,496 / 2,528 | 1,904 / 1,895 / 1,919 |

The original peak was not replaced or averaged away. Repeated controls found no reproduced frontend regression; **this is not a claim that every interaction is stably subsecond**. Authentication preparation sometimes returned to login with zero business calls; those preparations were retained separately and authenticated measurements started only after sign-in. Same-account login was verified not to revoke other sessions; the preparation failure's exact cause remains unproven.

## Screenshots and boundaries

Actual, unedited synthetic-data screenshots: [Chinese dark overview](evidence/dashboard-module-loading-20261028/overview.zh-CN.dark.jpg), [expanded Sessions](evidence/dashboard-module-loading-20261028/sessions.zh-CN.dark.jpg), [English](evidence/dashboard-module-loading-20261028/overview.en-US.dark.jpg), [Japanese](evidence/dashboard-module-loading-20261028/overview.ja-JP.dark.jpg), [Chinese light](evidence/dashboard-module-loading-20261028/overview.zh-CN.light.jpg), [390px](evidence/dashboard-module-loading-20261028/overview.zh-CN.light.390.jpg). The temporary viewport/language/theme were restored. Primary cards stack at 390 px; the existing global header overflow remains, so full mobile acceptance is not claimed.

The [receipt](evidence/dashboard-module-loading-20261028/receipt.json) pins captures, hashes, counts, timings and limitations. Private original bodies/cookies are excluded. Final runtime receipt SHA: `f132b855738478cfdb986e17364bc8dab941eef29d10399a82d6a46678e48095`.

This phase adds no dependency, database migration, persistent feature flag, ingestion change, or new bulk-original API. Keep the prior frontend artifact as functional rollback; its actual rollback/restoration was verified. SeekDB runtime, VM/cold-cache behavior and C10/C20 sustained capacity were not rerun in this frontend-only phase. Earlier engine-specific evidence and unresolved limits remain in the original report. No production release, merge or issue closure is claimed.
