# Native query performance contracts

Primary dashboards request `include_cost=false` on native Trace metrics. The default remains the complete pricing contract for existing API clients and detailed cost inspection. Numeric-only results expose `costsIncluded: false` and omit `cost`, `costCoverage` and `knownCostCalls` recursively; omitted cost is neither zero nor an unknown measurement. Counts, tokens, coverage, groups, time buckets and latency retain canonical semantics. Unsupported predicates keep the complete-window fallback.

Trace and Session list widgets focus on usage, latency and execution metadata. Their API responses retain published canonical cost fields for compatibility; removing a primary widget does not remove those stored fields or the detailed pricing contract.

Trace page requests bind an authorized `agent_uuid`. An exact matching Agent-resource equality is redundant and is normalized for old clients; conflicting identities and other resource predicates retain their original semantics. This does not replace Space/Agent authorization or per-Span scope checks.

## Session status evidence

Canonical Session responses expose `trace_status_counts` with `unset`, `ok` and `error` counts over the root statuses of every Trace in the selected Session, time window and Agent scope. These are complete-session counts, including turns outside the matching filter subset. Raw and accelerated readers use the same aggregation: any root ERROR yields `session_status=error`, all explicit root OK yields `ok`, and otherwise the result is `unset`. Child retries do not replace a Trace root's outcome.

UNSET is a valid default protocol value, not proof of missing telemetry. The Session UI describes unconfirmed outcomes as partially or entirely unknown and uses complete counts to distinguish partial from all-error results. An absent Session-status field is presented separately as not reported; an unrecognized value is not treated as UNSET. Older responses without counts retain the recognized aggregate state without inventing a breakdown. The counts are added when forming the response, so they do not require rewriting persisted projections, changing the projection context or migrating storage.

## Public list-filter bounds

Session total-Token minimums start at 100; list-duration minimums start at 1 ms, and count minimums start at 1. Lower bounds above `2**53-1` are rejected before a data-plane request rather than triggering a numeric-precision fallback. Invalid restored links remain editable and block list/facet requests until corrected or cleared. Conditions are never silently clamped or removed. Recorded zero, unknown usage, UNSET status, equality-to-zero predicates and local Span filters retain their own semantics.

The data-plane optional `content_complete_v1` STORED generated column preserves the existing private `_contentComplete` semantics and updates whenever an old or new publisher writes `row_json`. Its additive initializer does not reset source/projection rows, coverage or maintenance generation. Each content-capable query verifies the column definition in its read snapshot; missing or incompatible storage keeps JSON-based completeness checks. Stale revision, boundary and authorization proofs remain unchanged. Merely returning 200 rows does not limit these full-window proofs.

Positive Trace total-Token minimums can reuse verified stored Token helpers because tool-only zero and canonical unknown both fail a positive minimum. This equivalence does not justify accelerating equality-to-zero or null checks through the same helper. Full-text candidate filtering and vector semantic search are separate future work; this patch does not add a production full-text index or change literal substring semantics.

## Oversized source maintenance

A declined v1 source snapshot can retry `GET /api/v1/ai-vision/traces/read-model/source-page`, contract `native-trace-source-pages-v1`. Responses are at most 2 MiB, with base64 byte fragments, exact per-Span offsets/lengths, source byte/count proof and an opaque continuation. The complete reconstructed source is limited to 128 MiB. Every page checks source revision, physical scope and maintenance generation; the consumer verifies exact count/byte totals and publishes only after the final fragment. Publication retains revision/generation compare-and-swap. Legacy v1 transport limits are unchanged.

Large root IO can be omitted from the derived full-content fields while retaining complete numeric facts and bounded display previews. Such documents carry private `_contentComplete: false`. Metadata-only pages can use them, but content searches and requests for full content must decline or use the complete raw source. A truncated prefix must never become a successful full-text search result. Original telemetry is unchanged. Detail reads remain the route to full messages.

The projection context advances so managed maintenance automatically refreshes existing documents with operation previews and content-completeness evidence. No manual database migration or new deployment switch is required for these JSON-document supplements.

## Recent events

`operation-page?include_facts=true` returns only selected typed operations with revision-bound token facts and bounded IO previews. The caller validates scope, generation, ordering, counts and preview bounds. It avoids hydrating all selected Traces. Event rows identify `content_mode: preview`, `input_truncated` and `output_truncated`; clients must not present these as complete content. Older or unready data planes keep the existing verified fallback. Primary event tables display operation metadata and do not display these preview bodies.

The existing emergency raw-query mode and maintenance stop remain available. Capability/readiness failures preserve explicit fallback; malformed successful responses and authorization failures do not silently switch readers.

A versioned, authenticated `/read-model/capabilities` probe gates optional event parameters and incomplete-content publication. A missing endpoint retains older storage behavior; authorization and malformed-success errors remain explicit. Managed refresh keeps only the latest 200 operation previews per Trace, independently of the complete numeric facts. A complete compact snapshot can also supply these previews when typed SQL is unavailable; this does not enable unsupported database engine versions.

Supported engine version requirements remain unchanged. Application upgrades do not upgrade an existing database. The source transport bounds are distinct from database ingest packet/write-timeout limits; accepting an OTLP HTTP request alone does not prove that all Spans were persisted. Performance validation must verify raw storage counts before declaring ingestion or projection successful.

## Original input evidence

An explicit `inputEvidence` projection requires original observations and uses the complete native Trace-page path. Summary rows are not substituted for that evidence. The historical seven-field summary-page results do not measure this newer request. Its latency and concurrent SQL-timeout limits remain open; see the [validation report](../../../docs/testing/native-query-performance-20261028.md).

## Proposed layered-capacity decision

The [cross-component layered-capacity ADR](../../../docs/adr/trace-data-flow/layered-capacity-isolation.md) is a proposed target for ingestion, accepted storage, derived views, retrieval and browser boundaries. Its [design review](../../../docs/adr/trace-data-flow/reviews/layered-capacity-isolation-oceanbase-20261107.md) records the current proposal revision and resolved contract questions. The [comparative gap assessment](../../../docs/adr/trace-data-flow/reviews/langfuse-langsmith-gap-20261107.md) separates merged main, local work and deferred delivery packages. It does not replace the implemented contracts above until its decision and implementation statuses are updated with actual evidence.
