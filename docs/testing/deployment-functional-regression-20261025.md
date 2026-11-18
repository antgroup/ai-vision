# Functional regression after canonical deployment integration — 2026-10-25

No regression was found in the exercised Trace/Session search, filtering, complete-session reading, automatic projection maintenance or functional recovery paths. This run adds actual canonical Compose backend execution to the earlier [configuration integration checks](deployment-query-integration-20261025.md). No product implementation change was needed.

## Tested combination

Product `dc1a46675e891e3fdd2e20c9c74d754c09c3f080` integrates issue #88 / PR #94. The baseline is pre-integration product `2bdb1fbb4b5af0082211174334d32231bf901437`; SigNoz remains `22e936bf647ba2042810d71fa200e6cbbb468aab`.

Both backend images use identical cached Python 3.11.14 dependencies and matching `uv.lock` bytes. Current tracked backend source was copied into a clean application directory. Four current backend containers used the canonical external-seekdb Compose template, real image entrypoint, Gunicorn 21.2.0, managed projector, healthcheck and 60-second termination grace. Metadata/endpoints/logs were replaced by owned test fixtures; dependency services were intentionally skipped. Loaded source hashes, query modes, health and absence of the recovery key were independently read back from every current container.

The query transport uses the actual Go handler, parser, migration helper, store and SQL on two retained test clones. Each clone contains 109,858 raw Spans and 36,612 canonical sources/projections, covering five synthetic profiles and Eino, DeepAgents, DSH and Codex multi-turn captures. Private fixed authentication replaces full SigNoz authentication. SQLite metadata is copied from the retained test configuration; no existing product metadata or original service is updated.

## Fresh checks

| Check | Result |
| --- | --- |
| Backend | 195 tests passed, including query/session semantics, health, integration/auth boundaries and supervisor lifecycle |
| Frontend | 179 tests passed; TypeScript typecheck passed. Frontend and query implementation trees are identical before/after the merge |
| Deployment contracts | 32 direct Compose cases, 20 CLI product exports, five source exports, six precedence/state cases, independent Kubernetes rendering/identity checks; 28 focused Go tests passed |
| Deployment Python | 55 passed, 13 optional integration skips; generated 36-file assets and four Compose variants current |
| Core and expanded real-store reads | 666 successful requests: per engine 126 core, 204 expanded and three full-window raw recovery reads |
| Historical business parity | All 252 core response hashes match the pre-integration runtime; accelerated and raw paired responses also match |
| Concurrent health | Eight indexed 10k Session reads and four health/readiness probes passed; each probe overlapped four actual client HTTP requests |
| Automatic refresh | Four known source rows per engine marked dirty; only the normal auto workers healed them, with 64 frozen reads before/after matching |
| Active-request shutdown | A real 10k raw Session read completed through SIGTERM; clean container exit, then authenticated Eino Session reading passed after restart |

Expanded cases include input/output search, exact/partial Session identity, status, duration, node count, reported user, tags, combined predicates, cross-turn rejection, sibling-instance rejection, unknown/empty results and pagination. Session detail, complete trace membership, usage, full first/last IO and source-node references match independent frozen expectations. Token ≥0 returns 9,090 Sessions and excludes unknown usage; ≥100 returns 839; the unfiltered scope contains 10,000.

Raw recovery list/Token ≥100/Token ≥0 completed in 19.6/21.6/22.0 seconds on seekdb and 20.2/18.8/19.2 seconds on CE, one observation per case. These are complete bounded recovery reads, not ready-list latency distributions.

## Runtime and performance follow-up

Health/readiness returned HTTP 200 within the existing three-second probe budget. With four indexed 10k queries overlapping, health took 1,058.5 ms on seekdb and 765.3 ms on CE; readiness took 3.7/5.7 ms. This bounded check does not establish saturation capacity or an SLO.

The standard workers healed four dirty sources within 1,193.9 ms on seekdb and 1,252.5 ms on CE. No manual or per-Space projector was started, and no raw row, source revision or maintenance latch was changed. The active-request stop took 26.5 seconds, exited zero without OOM, and preserved the complete response. This was an actual request drain within the 45/50/60-second budgets, not a full-length 45-second drain test.

For a fair startup-runtime comparison, exact baseline/current images use the same dependency layers, copied metadata layout, SQL transport and stores. Only one baseline engine runs at a time. Raw readers and other foreground replays are stopped. Serial AB/BA reads are interleaved after one warmup for each case/version, with five measured observations per cell. Every response passes the frozen oracle and matches business hashes. The baseline retains its original runserver/projector supervisor; current uses Gunicorn/projector. Baseline lacks the new readiness route, so its expected healthcheck incompatibility is excluded from the timing comparison and its existing health route gates startup.

| Engine | 10k Session query | Baseline median ms | Current median ms | Baseline min–max ms | Current min–max ms |
| --- | --- | ---: | ---: | ---: | ---: |
| seekdb | List | 830.6 | 822.0 | 816.0–897.9 | 811.9–873.8 |
| seekdb | Tokens ≥100 | 769.4 | 759.4 | 746.6–775.6 | 752.2–774.2 |
| seekdb | Tokens ≥0 | 849.1 | 854.5 | 837.2–862.4 | 838.8–885.5 |
| ce | List | 552.1 | 551.3 | 535.6–574.0 | 502.2–583.1 |
| ce | Tokens ≥100 | 511.6 | 507.8 | 470.6–585.0 | 470.7–527.5 |
| ce | Tokens ≥0 | 531.5 | 559.4 | 507.2–566.8 | 529.9–581.8 |

These small paired samples show no material latency regression in this setup. They are descriptive smoke evidence, not tail-latency, concurrency, capacity or cross-engine rankings. Earlier n=20 measurements remain bound to their original runtime in [the preceding performance report](standard-query-defaults-20261025.md); they are not relabeled as these container measurements. Initial 2–3-second indexed observations during a different test phase were not reproduced in this paired comparison; no causal attribution is inferred.

## Integrity, review and limits

Before/after all-column raw fingerprints match for every retained scope on both clones. Source/projection counts, clean eligibility, maintenance generation/schema/flags and ordinary global SQL deadlines are unchanged. Original service IDs, start times, restart counts, configuration and mounts are unchanged. Only task-owned backend/transport containers and their private metadata copies were created or stopped. Baseline/current image tags are local test artifacts, not published candidates.

Independent review found no concrete startup/configuration/query blocker. Invalid Compose/CLI control strings are still forwarded at export time, while Kubernetes rejects them; valid values are the supported runtime cases above. Initial fixture setup/identity errors were corrected with failures retained outside Git; no product code was changed to make these checks pass.

This is prepared Mac-host/Docker Linux ARM64 backend evidence. seekdb runs as amd64 under emulation; CE runs arm64 with different resource settings. It does not accept released artifacts, a cold/full-stack installation, new organization bootstrap against a complete SigNoz service, full SigNoz authentication, live Kubernetes, WAN latency, production capacity or new model/telemetry ingestion. Shared maintenance OFF/resume and full historical rebuild are not repeated here; their unchanged SigNoz implementation and earlier real-store recovery evidence retain the documented long CE recovery cost.

Sanitized per-request samples, runtime source hashes, paired summaries and integrity fingerprints are in [the evidence](evidence/deployment-functional-regression-20261025.json). Private credentials, raw IO, endpoint identities and host paths are excluded. The tested runtime source is the product SHA above; subsequent report-only commits do not constitute another runtime.
