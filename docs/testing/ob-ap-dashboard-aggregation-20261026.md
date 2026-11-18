# OceanBase dashboard aggregation verification — 2026-10-26

The completed change moves common dashboard statistics into typed OceanBase contribution tables and SQL aggregation, and selects recent operation keys before fetching their contents. On the prepared 10k-Session dataset, five concurrent dashboard APIs completed in a median **3.263 s**, compared with **10.933 s** for the preceding compact-window implementation, a **70.15%** reduction. Complete business-response hashes match. This is API completion time, not browser render time.

The implementation and evidence extend the [preceding dashboard correction](dashboard-query-performance-20261026.md). The previous report remains a record of that earlier stage; its outstanding large-window statistics cost is addressed here. Measurements used uncommitted source snapshots in the product and SigNoz task branches; source delivery is recorded separately from this measurement phase.

The subsequent [lightweight seekdb verification](seekdb-dashboard-compatibility-20261026.md) records its separate SQL/DDL compatibility path and same-seekdb measurements. It does not change the CE measurement boundaries or attribute CE results to seekdb.

## Measurement boundary

- Product base: `historical-source-revision-redacted`; SigNoz base: `d6d5d94cdbfc70885f7218254464d9043ea6f326`. Final reviewed/runtime file hashes and receipt hashes are in the [portable evidence](evidence/ob-ap-dashboard-aggregation-20261026.json).
- Prepared local OceanBase CE **4.4.2.1**, Linux/arm64 under Docker, **4 CPU / 8 GiB**. Global SQL timeout remained **10 seconds**. Ordinary deadlines, resource settings and authentication rules were not changed.
- The same frozen Eino, DeepAgents, DSH and Codex data supplies **12 native Traces / 28 operations**. Scale data supplies **10,000 Sessions / 30,000 Traces / 90,000 Spans**. Together the task fixture has **30,012 Trace contributions / 60,028 operation contributions**. Models were not called again in this phase.
- Real Django APIs, production Go handlers/publisher and real CE SQL were exercised through a task-local transport with a fixed organization claim. This does not verify hosted production authentication. Both routes used identical physical scope, stored data, fixed time windows and direct loopback with `NO_PROXY`.
- The control is the preceding compact-window route on the same current stored projections, which already contain the optional `_statistics` envelope. Canonical JSON before that envelope was **312,202,478 bytes**; current stored projection JSON is **328,298,739 bytes**. This is a controlled route comparison, not a byte-identical pre-feature storage comparison.
- Formal timing ran without task writers, fixture preparation, DDL, builds or test suites. All three samples, including first observations, are retained. Three samples establish neither tail percentiles nor a production SLO.

## Five-request dashboard regression

The five actual APIs ran concurrently over five independent connections using one authorized login session. Baseline/candidate rounds were interleaved, with **n=3 per route**. Timing/path metadata is excluded from complete business-body hashes; all business hashes match the control.

| Request | Compact route median | Final route median | Final range, n=3 |
|---|---:|---:|---:|
| Statistics | 10.815 s | 2.497 s | 2.337–2.529 s |
| Operation trend | 10.498 s | 2.517 s | 2.347–2.542 s |
| Recent event stream | 10.929 s | 3.260 s | 3.106–3.305 s |
| Trace list, 200 | 1.168 s | 1.324 s | 1.181–1.505 s |
| Session list, 10 | 1.278 s | 1.540 s | 1.439–1.706 s |
| All five completed | **10.933 s** | **3.263 s** | **3.109–3.310 s** |

The baseline all-request range is 10.910–10.944 s. The approximately **70%** page-request improvement does not mean every section became faster: the concurrent Trace and Session lists increased by **156 ms** and **261 ms** at the median. They still share the single-node database with aggregation and hydration. No universal list-speedup claim is made.

Across those five requests, database-to-Go read bytes decreased from **334,794,440** to **4,696,999–4,697,355**; Go-to-Python HTTP response bytes decreased from **89,413,042** to **3,337,450**. These are summed request transport counters, not browser payload or physical disk-read measurements. Decimal MB is used when summarizing them as 334.79 → 4.70 MB and 89.41 → 3.34 MB.

## Individual requests and storage layout

Final-source row-storage requests, measured separately from the concurrent rounds:

| Scope | Statistics median, n=3 | Trend median, n=3 |
|---|---:|---:|
| Eino | 26.16 ms | 23.49 ms |
| DeepAgents | 32.68 ms | 20.66 ms |
| DSH | 18.38 ms | 20.95 ms |
| Codex | 17.01 ms | 27.33 ms |
| 10k Sessions | **1.593 s** | **1.601 s** |

The final 10k statistics range is 1.480–1.869 s; trend is 1.542–1.729 s. Native windows are small: Eino statistics included a first observation of 191 ms, and not all small-window routes outperform the compact control. Full samples are retained in the evidence.

One final 10k recent-event observation took **1.062 s**, with **one operation-page request and two batch-snapshot requests**, transferring **660,813 bytes** upstream and reading **1,051,018 database bytes**. The latest 200 events required only **104 distinct Trace snapshots**, fetched as 100 + 4. Full ordering and IO matched the control. Four native event-stream observations were 24.9–41.2 ms; these are single observations, not medians or tail statistics.

Separate same-fact row/hybrid/column runs used the final three-query aggregate shape, n=3 each:

| Typed fact layout | Statistics median | Trend median | Two fact tables' SSTable `SIZE` |
|---|---:|---:|---:|
| Row | 1.567 s | 1.574 s | 2.93 MB |
| Hybrid | 1.631 s | 1.623 s | 5.76 MB |
| Column | 1.625 s | 1.651 s | 4.34 MB |

The read ranges overlap. Only task tables were synchronously converted; actual `CO_MAJOR` / column-group SSTables and unchanged full fact hashes were verified. `SIZE` sums both fact tables' SSTable metadata, excluding MDS; it is not total database/disk/backup size or a general compression ratio. New tables therefore explicitly default to row storage (`WITH COLUMN GROUP(all columns)`), while existing hybrid/column tables remain accepted without forced conversion. No production covering index, forced hint, parallelism setting or new environment switch was introduced.

## Implementation and correctness

1. **Typed contributions and additive migration.** A separately leased optional schema preserves ordinary projection readiness, canonical/price context and maintenance generation. Startup/retry and recovery initialize it without resetting ready lists. A STORED scalar receipt derives from the source projection document on every write, including old publishers. The existing projector atomically replaces all contributions for one Trace under its source revision and maintenance fence. Missing statistics are incrementally populated; unsupported dimensions, precision or document size store an explicit ineligible receipt without rejecting the ordinary summary or re-enrolling forever.
2. **Aggregate inside OceanBase.** Common unfiltered authorized windows use three data SQL statements: combined all-candidate completeness plus Trace summary; a bounded sorted latency vector; one `GROUPING SETS` statement for usage, type, model/provider, time and operation partitions. The actual production Go-driver plan shows one authorized operation input into `HASH GROUP BY → EXPANSION → HASH GROUP BY`, instead of five repeated joined branches. This is not a claim of three physical scans: authorization still includes per-Trace raw range accesses. OceanBase documents [`GROUPING SETS` in MySQL-compatible SELECT](https://docs.oceanbase.com/oceanbase-database/V4.4.2-select-mysql-compatible-mode); automatic use is conservatively gated to OceanBase 4.4+, with actual execution verified only on CE 4.4.2.1.
3. **Retain canonical semantics.** Known subtotals, missing usage, nested-call billing ambiguity, Decimal costs, binary dimension identity, root error count and exact nearest-rank P95 are preserved. To retain Python's exact sorted floating-point mean and JSON P95 type, the response includes a sorted latency-only vector: **30,000 values / 144,001 reserialized bytes** in this fixture. The response is bounded, not constant-sized. Coexisting null/empty group keys decline to the existing window to preserve stable canonical ordering. Content/dynamic attribute predicates also retain the complete-content path.
4. **Bound recent-event contents.** A complete-scope repeatable-read gate selects at most 200 latest keys, ordered by descending time and binary IDs; only selected complete sources are hydrated in batches of at most 100. Every source must match scope, revision, count and maintenance generation. Revision reuse after reset is possible, so generation is essential. Any valid revision/generation drift discards the entire selection, including earlier successful batches, and rereads one complete raw window. Private proofs never enter public event rows.

Statistics transport alone decreased from **330,993,705 → 432,263 database bytes**, and **86,412,942 → 146,762 upstream bytes**. Most of the remaining aggregate response is the latency vector. The gain comes from typed fields, aggregation pushdown and bounded content hydration; the experiment does not establish column storage as its cause.

## Staged validation and functional recovery

- Final Python behavior suite: **198 passed in 8.826 s**. Final Go checks: **193 top-level tests passed / 488 pass events including subtests**, three affected packages; **nine real-SQL tests skipped without a DSN** in that offline suite. Those skips are distinct from the task's separately executed CE checks.
- Final statistics/trend comparisons: **30 complete business hashes matched** over final-source repeats; **24 native auto/raw comparisons** and **15 raw/compact contribution comparisons** also matched. The whole 30,012/60,028 contribution fixture was hashed before and after. Event streams match full ordering/contents for all four frameworks and the 10k scope; all five concurrent responses match their baseline business bodies.
- Real initializer checks cover fresh and interrupted optional DDL, released leases, unchanged ordinary list bodies/state and idempotence. A fresh 12-Trace initialization took **4.789 s**, with a **21.6 ms** idempotent retry. This does not measure initialization of a populated production history.
- Actual task-table probes cover absent facts, dirty selected and unselected Agents, maintenance OFF, raw mode, partial-Trace boundaries, limits 1/2/200, empty selection, old-interface 404, source-revision drift and same-revision/different-generation ABA. Missing facts preserve full Trace/Session list bodies and indexed readiness; one worker publication restores SQL aggregation without changing the source revision/dirty state. Numeric dimensions, oversized tokens and high-precision costs retain canonical fallback and leave no repeat-projector loop.
- An actual custom Eino attribute matched **3 Traces / 20 Spans / 5 model calls / 4 tools** through raw recovery; a deliberate nonmatch returned zero with the same raw business body. Unsupported-filter checks happen before empty-schema validation, so discovered telemetry attributes remain searchable.
- Independent source review resolved seven findings plus an integrated P95 JSON-type difference; the final 14 Python and 19 Go file hashes match reviewed and running code. The reviewer inspected lab receipts but did not independently rerun SQL or performance tests.

The only emergency controls remain `AIVISION_QUERY_MODE=raw` and `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED`, with the existing deployment aliases `BACKEND_QUERY_MODE` and `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED`. Raw bypass, missing capabilities, ineligible/dirty contributions and boundary cases retain complete bounded recovery. Older compact windows lacking a maintenance-generation proof use raw for split-step content hydration; atomic metrics/list reads acquire no new dependency solely because that proof is absent. Authentication, transport, schema-access and malformed-success errors remain errors.

## Costs, limits and retained diagnostics

- **Extra projection maintenance:** the real publisher plus database took median **902 ms → 1,245 ms**, about **+38%**, for the same 12 native Traces / 28 operations, n=3 interleaved, in a fresh task prefix without the experimental index. Ranges were 838–1,251 ms and 1,234–1,405 ms. This excludes HTTP, JSON decode and startup. It measures an asynchronous projection batch, not raw ingestion throughput, production TPS or historical backfill capacity. The unchanged final publisher was measured before the read-only page/generation additions.
- **Recovery can remain slow:** unsupported filters, missing/ineligible facts, boundaries and drift intentionally select the existing complete window. Typed catch-up is automatic, but full product-history catch-up throughput was not benchmarked. Bounded fixture preparation took **744.545 s**, using 100-row transactions after a large preparation statement hit the existing deadline; that import is not a product migration benchmark.
- **Intermediate regression was caught:** statistics-only optimization left a full-window event stream and increased five-request wall time from median 8.075 s to 8.676 s. The bounded operation-page follow-up removed that amplification before final acceptance. Eight-query attempts and literal-cursor SQL profiles remain diagnostic; endpoint decomposition uses the production Go prepared protocol. No first-sample result is substituted for the final rounds.
- **Harness login throttling:** a repeated login series returned 429 before query timing. The failed receipt remains; natural expiry and reuse of one normal authorized login completed the remaining rounds. No timed query sample was discarded and no authentication setting was changed.
- No browser render/screenshot, cold installation, VM, live Kubernetes, released artifact, Enterprise execution, seekdb AP execution, multi-replica behavior or production-load test was performed in this phase. The frontend did not change for this AP follow-up. Earlier UI verification remains in the preceding report.

## Preservation and evidence

Original seekdb and CE data each retain **109,858 raw Spans / 36,612 source and projection rows**, with identical full fingerprints, clean state and maintenance settings. The original nine containers retain identical IDs, starts, configuration and mounts; global SQL timeouts are unchanged. Task Django listeners and the two task query containers were stopped. All task proof tables, snapshots and containers were retained for reproduction; the experimental covering index remains invisible and is not adopted.

Automatic approval review rejected creating an additional `%`-host test account before execution because it would expand the authentication boundary. No account or grant was created. The safe alternative reused the already-authorized test connection, with mutations hard-bound to task prefixes and original tables read-only.

The portable [evidence JSON](evidence/ob-ap-dashboard-aggregation-20261026.json) contains final samples, source/log hashes, correctness gates and preservation results without credentials or message contents. Full local receipts are retained under `outputs/ob-ap-metrics-20261026/`: `final-summary.json`, `source-binding-exact-final.json`, `python-generation-fence-validation.json`, `go-implementation.json`, `independent-review.md`, `operation-functional-gates.json`, `operation-edge-gates.json`, `fresh-row-initializer.json`, `integrity-before.json`, `integrity-after.json`, and `cleanup.json`. Historical statistics-only results remain in `statistics-stage-summary.json` and are superseded by the final integrated rounds. No commit, push, PR, image publication or production deployment was performed during the measurement phase.
