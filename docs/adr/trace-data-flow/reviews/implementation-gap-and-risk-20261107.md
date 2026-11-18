# Layered Trace Capacity: Implementation Gap and Risk

Historical assessment before research incorporation. See the [current comparative assessment](langfuse-langsmith-gap-20261107.md) for the revised delivery packages and main/local distinction.

Date: 2026-11-07, Asia/Shanghai. This assessment compares the current local candidate with the [original proposed ADR](../layered-capacity-isolation-20261107-1540.md), SHA-256 `8093438bae3e04bc6be05cf09697719f939bbd704f994d487d74609ef2e0d62f`. The baseline is AI Vision `historical-source-revision-redacted` plus the existing local capacity fixes, paired with the locally fixed SigNoz source based on `43a4ad8522d5c031248e4ef6c531b74465278ad8`. It is not a claim about the deployed production version.

## Assessment

The gap is substantial: a new cross-component data path, built on reusable correctness and resource-protection foundations. It is not a threshold adjustment and does not require replacing the entire platform. Overall implementation risk is **medium to high** before staged validation. The design permits risk containment; current evidence does not yet prove new SDK compatibility, write/resource costs or browser isolation.

Design review with no remaining contract blockers means the target is sufficiently specified for planning. It does not mean low implementation risk, tested defaults, production readiness or maintainer acceptance. No completion percentage or calendar estimate is justified before the high-risk integration paths are exercised.

## Current-to-target mapping

| Boundary | Reusable current implementation | Actual gap | Risk |
|---|---|---|---|
| Identity and semantics | Token authentication, authoritative Space stamping, Agent routing, canonical root/usage/unknown rules and golden cases | Maintain the same rules in a bodyless catalog/reducer and sparse UI | Medium; semantic and authorization drift is unacceptable |
| Ingress and acknowledgment | Existing HTTP/gRPC receiver, exporter and normalization pipeline | Checked preallocation codec/token/wire limits, byte packing, real request-owned partial-success receipt and synchronous commit acknowledgment | High; changes transport/allocation and sender latency/backpressure |
| Writes and storage | Raw primary keys, source lock/revision and existing normalization | Trace/chunk transactions, full-value no-op, reversible quota, atomic raw/index/chunk/catalog maintenance independent of rich-worker OFF | High; retries, updates, ownership and commit failures can corrupt derived truth if wrong |
| Directory and migration | Source enrollment/revision machinery and schema readiness patterns | New catalog/Span index/content sidecars, resumable enrollment, independent readiness and 10-minute as-of catalog journal/cleanup | High; coverage, hot locks, history retention and storage amplification |
| Projection and query | CAS/generation/context proofs, partial errors,32/64 MiB request budgets, bounded source-fragment protocol | New summary/header/topology/content API family, byte continuation, per-reference snapshots and bounded reducers | Medium to high; old source-pages still reconstruct the complete Trace and are not an interactive pager |
| Presentation | Existing list/drawer/components, canonical labels, preview/unknown/partial states | Bounded stream reader, sparse topology model, active-view/cache ownership, virtualized/worker-limited rendering and selected content | Medium to high; current parent hooks assume a complete Span set |
| Other consumers | Existing canonical aggregates and strict completeness/error contracts | Explicit Session/Labs/dashboard/CLI/export/diagnosis adaptation and separate evidence/materialization budgets | Medium to high, by consumer; Trace UI success does not cover them |

The current product source has no implementation of the proposed `trace_catalog_v1`, `span_index_v1`, `span_content_chunks_v1`, `catalog-page` or `native-trace-layered-v1` identifiers. The ADR introduces four main sidecar families plus epoch/contribution state and approximately nine public route types with private counterparts. Some helpers can be shared; the main data/control flow is new.

Useful current source anchors:

- [Gateway configuration](../../../../antcollector/conf/antcollectorsignoz/gateway.yaml): asynchronous batch and sending queue remain enabled.
- Paired SigNoz `pkg/ingest/ingest.go:308-362`: one transaction for the mapped request, with tracking conditional on rich-maintenance state.
- [Native detail](../../../../ai-vision/vfe/src/components/traces-otel/NativeTraceDetail.tsx): whole-detail fetch at402 and full Span/tree derivation at428-429; process, filtering, usage and export also depend on complete data.
- [Paged worker source](../../../../ai-vision/aivision-py-be/apps/core/trace_query/signoz/source_pages.py): pages are validated but accumulated into a complete `spans` list; it cannot simply become the interactive content reader.

## Highest-risk decisions and exit criteria

### 1. Synchronous ACK compatibility

Pi Trace exporter sets **1,000 ms** at [telemetry.ts](../../../../ai-vision/agent-plugins/pi/observ-pi/src/telemetry.ts); OpenCode Trace exporter sets **30,000 ms** at [telemetry.ts](../../../../ai-vision/agent-plugins/opencode/observ-opencode/src/telemetry.ts). These apply to their configured Trace endpoint. The current gateway independently retries for up to five minutes. A synchronous native commit changes what an SDK's timeout covers.

Before enabling the new default, exercise actual HTTP/gRPC SDK delivery, slow commits, network loss after commit, partial rejection, shutdown/flush and retry behavior. Pi's short-timeout path must have a compatible sender policy or another explicitly reviewed delivery policy; do not assume the SDK retries or silently drop its errors. This change should have an independent cutover gate rather than be bundled blindly with the new UI.

### 2. Atomic accepted-data and catalog updates

Fault injection must prove raw/source/index/chunk/catalog agree after a successful accepted chunk; permanent rejection cannot roll back a healthy different Trace; changed/no-op/replayed IDs follow the documented policy; a failed commit is not acknowledged as success. Verify quota subtraction on corrected updates and catalog tracking while rich maintenance is OFF. New tables reduce migration disruption, but do not remove added write latency, storage, locking and failure surfaces.

### 3. Canonical semantics and authorization

Compare old canonical results with the new directory/reducer on normal and adversarial gold: late parent, multiple roots/cycles, null-shadow updates, repeated correction, mixed ownership and revocation. Pending fields are explicit unknowns and must not become fake filter nonmatches, roots or zero usage. A sparse index cannot be cast to an ordinary complete Span array.

### 4. Snapshot history and online backfill

Verify active writes during enrollment, unknown legacy body/accounting, writer cutover, hot-Trace updates while another list paginates, original snapshot expiry, and validTo-based cleanup. Read back actual coverage and test interruption/resumption. Query plans and measured resource use must show that listing avoids full-window Span-core/body hydration; correctness alone does not qualify performance.

### 5. Real browser resource ownership

Measure decoded bytes, retained/peak heap, mounted nodes and long tasks under giant content, many small Spans, deep/wide values, multiple requests and view switching. Confirm pinned values stay charged after cache eviction, cancellation leaves server permits owned until cleanup, and limits stop one pane while sibling navigation remains possible. A screenshot or an encoded-byte counter is insufficient proof.

## Staged delivery and rollback

| Stage | Entry/exit gate | Default-path and rollback boundary |
|---|---|---|
| Protocol/admission validation | Actual wire outcomes and SDK timing/retry cases pass; no logs/metrics regression | Keep previous default until proved; a codec/config-only patch is not a commit-ACK implementation |
| Additive write/index work | Atomicity, quota, revision/owner equivalence and coverage readback pass under faults | Old reads remain authoritative; failed shadow/index work cannot be exposed as current truth |
| Explicit layered provider | Catalog/header/topology/content/proof and exact-byte continuation pass against real DB | New versioned capability only; no implicit full-window fallback on failure |
| Controlled Space UI | One complete list-to-selected-content flow passes interaction, browser-resource and Linux gates | Enable per Space/capability before broader rollout; preserve an explicit legacy mode with its old limitations |
| Consumer expansion | Each Session/Labs/export/diagnosis contract has its own completeness/isolation evidence | Do not mark the whole ADR implemented when only the ordinary Trace drawer is migrated |

The synchronous ACK and catalog-history changes are separate high-risk validation packages; do not combine every default cutover in one release. Stages can have separate focused PRs while sharing the same final ADR contract. Intermediate delivery is `partial`, not full target completion.

Read rollback disables the new capability/UI path and preserves raw/sidecars; it restores prior behavior and prior limits, not the new guarantees. Write downgrade first invalidates layered readiness and fences incompatible writers, then allows the old writer contract. Simply stopping dual writes while serving the new catalog is unsafe. Restoring the old async gateway also restores its old acceptance semantics; it cannot still be called durable ACK.

## Scope discipline

Use the ADR as the target architecture and explicitly stage its delivery. Do not turn every target capability into one release's indivisible change. Stable catalog snapshots, full content descriptor indexes and broad consumer migration are real work, not free consequences of adding limits. Any decision to defer or weaken a target behavior must be recorded as scope/contract revision or partial implementation, not silently removed from the acceptance gate.

The proposed capacity values need boundary tests, representative payload distributions and actual Linux/browser resource qualification before becoming production defaults. Existing30 API/309-test results remain useful baseline regressions; they do not validate this new design. This assessment used source inspection and independent reviewer short assessments only: no product edits, new test/load, SDK runtime test, VM operation or new screenshot was performed.
