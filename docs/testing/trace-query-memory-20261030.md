# Trace query memory containment: seekdb and OceanBase CE validation

Date: 2026-10-30. Local implementation and retest evidence; no image publication or production deployment is implied. [Machine-readable results and tested source hashes](evidence/trace-query-memory-20261030.json) accompany this report.

This is the initial PR snapshot. See the [follow-up analysis and regression results](trace-query-memory-followup-20261030.md) for the later streaming-metrics fix and additional SigNoz SQL optimization. Historical measurements below are unchanged. The follow-up corrected an extra `5ad` suffix in the evidence JSON's seekdb image reference; the tested local image was correct.

## Problem and implementation boundaries

Concurrent Trace queries can retain large upstream responses and reconstructed source batches in the API worker. The companion SigNoz change also removes full-window parsing of large Span payloads from native Trace pagination.

This repository implements:

- Streamed upstream reads, counting decompressed bytes and spooling before JSON decoding. Defaults: 32 MiB per response, 64 MiB cumulatively per request.
- Two admitted native-query requests per backend worker, with the lease held through response rendering and close. Worker threads share their parent budget. Capacity failures use explicit 3036/3037 errors and HTTP 413/429 rather than truncated success.
- Background source batches with explicit budgets, smaller retries after releasing failed batches, deferred oversized Traces without starving later work, and bounded projection-publication buffers.
- Effective seekdb `memory_budget` configuration, preserving the legacy `SEEKDB_MEMORY_LIMIT` alias. Default container caps are 4G for seekdb and 2G each for backend/SigNoz; supported Docker, Compose, systemd and Kubernetes paths carry applicable controls. Native host processes are not claimed to have container caps.

Deploy with the companion SigNoz implementation. Updating this repository alone does not replace the SQL in an already running SigNoz image. CE keeps `memory_limit`; the seekdb parameter replacement does not apply to CE.

## Database comparison

Both engines used 5,000 synthetic Traces / 5,000 Spans, a three-day window, and page size 50. The 4,950 non-page inputs were 64 KiB; the 50 page inputs were 16 KiB. Attributes and payload contained duplicate input bodies. Raw JSON totaled 652,074,800 bytes; stored metadata totaled 1,415,000 bytes.

Four direct SQL callers ran after separate database-process restarts:

| Engine / metric | Original SQL | Optimized SQL |
|---|---:|---:|
| seekdb 1.4.0.0: individual elapsed time | 3.198–3.281 s | 1.204–1.243 s |
| seekdb: sampled process RSS peak | 1,020.72 MiB | 600.43 MiB |
| seekdb: sampled cgroup peak | 1,047.56 MiB | 657.51 MiB |
| CE 4.4.2.1: individual elapsed time | 6.762–6.763 s | 1.806–1.825 s |
| CE: sampled observer RSS peak | 3,773.37 MiB | 3,074.53 MiB |
| CE: RSS increase above starting sample | 1,033.55 MiB | 389.72 MiB |
| CE: sampled cgroup peak | 3,942.10 MiB | 3,292.73 MiB |

All successful calls returned the same 1,689,241-byte envelope, exact count 5,000, IDs 4950–4999 and complete page input. CE additionally ran two warm rounds per variant: original 4.105–4.551 s versus optimized 0.639–0.672 s. All 24 CE responses had the same canonical content hash. No OOM events occurred.

These SQL measurements belong to the companion SigNoz implementation. The API changes here provide transport/admission containment; they are not the source of the SQL speedup.

## Environments and measurement limits

Both databases ran natively on Linux ARM64 with a 2-CPU cgroup quota. seekdb used a 4 GiB container and verified `memory_budget=2G`. CE used an 8 GiB container, `memory_limit=6G`, `system_memory=1G` and a 3 GiB test tenant. CE used an isolated host bind mount with a 2 GiB data file and 4 GiB log allocation after the Docker VM's disk capacity prevented the initial setup. Fixed image digests are recorded in the JSON evidence.

The original variant ran first. Process restarts did not clear the host page cache. seekdb's timed optimized SQL includes the encoded-page preflight; CE also includes the metadata-size preflight. Schema checks are covered by handler integration tests rather than timed SQL. Four direct database callers intentionally bypass the two-request HTTP gate. These are local observations, not API P95 or a database-engine ranking; memory budgets and storage layouts differ.

## Backend memory and validation

The backend fixture used Linux ARM64, Python 3.11.14/Django 5.2.12, a 2 GiB cgroup, two CPUs and a persistent four-thread pool across six rounds. It invokes the actual decoder, canonical projection, middleware and rendering, with fixture network streams rather than a complete deployed service stack.

| Fixture | Process high water | Results across six rounds |
|---|---:|---|
| 16 MiB response | 198.06 MiB | 12 complete successes; 12 explicit admission rejections |
| 128 MiB response | 60.54 MiB | 12 explicit byte-budget rejections; 12 admission rejections |

The oversized response is rejected before decoding, not served successfully in 61 MiB. This does not establish that the reported long-running leak has been reproduced or fixed.

Local source validation on the recorded implementation:

- 227 Django tests passed across transport, projection, pagination, authorization, native/OpenAPI and dashboard consumers.
- The CLI's full `go test -mod=vendor ./...` suite passed on Linux Go 1.25.14; targeted `native_distribution` tests/compilation passed.
- Deployment tests: 53 passed, 24 skipped for unavailable dependencies; six Linux seekdb lifecycle tests and ten installer tests separately passed.
- Embedded deployment assets and generated Compose checks passed.
- The companion SigNoz packages passed against real seekdb and CE. CE ran 11 real database cases, including historical metadata backfill, updates, explicit-null precedence, pagination and recovery.
- Before submission, source checksums were matched to the retested implementation; all 13 memory-budget regressions and the 37-file embedded-asset drift check passed again. This is local evidence, separate from hosted CI.

## Upgrade and unverified scope

Container limits require recreation through the supported lifecycle. Admission is per process/worker, so additional replicas multiply capacity. Large tables need a planned maintenance window and capacity for SigNoz's additive stored-column migration; the CE 5,000-row migration wrapper completed in 10.53 seconds, which is not a production sizing estimate.

Production traffic/capacity, multi-day resident-projector behavior, ingestion throughput overhead, live Kubernetes, freshly built service images, other database versions, distributed CE, x86 performance and browser error presentation remain unverified. No UI was changed. The original 7.8 GiB incident was not reproduced by this bounded fixture.
