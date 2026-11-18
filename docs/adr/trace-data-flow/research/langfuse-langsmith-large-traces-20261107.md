# Large Trace handling: Langfuse, LangSmith and AI Vision

Research date: 2026-11-07, Asia/Shanghai. Evidence: current official documentation, Context7 retrieval and fixed-source inspection. No platform account, production service, browser acceptance or load test was exercised. This is research input to the proposed AI Vision ADR, not an accepted design amendment or an implementation claim.

## Assessment

Both products support separating lightweight queries from expensive content, and setting different limits on ingestion, topology and derived views. Neither establishes a general guarantee that every accepted Trace can be fully materialized online or that every deterministic item failure leaves an entire mixed batch unaffected. AI Vision should borrow the separation of representations and explicit capacity states while retaining its own stronger isolation and accepted-content requirements.

## Capacity boundaries: compare the object, not just the number

| Product and layer | Verified boundary | Interpretation |
| --- | --- | --- |
| Langfuse Cloud API | 5MB per request and 5MB per response | Official FAQ does not define MB radix or compression accounting. This is not a cumulative per-Trace bound. [Official limits](https://langfuse.com/faq/all/api-limits). |
| Langfuse self-host OTel | Configurable body bound defaults to 536,870,912 bytes, or 512 MiB; encoded and expanded bodies are checked | A request guard, not a recommended safe process-memory budget. Cloud's gateway policy is a separate layer. [Pinned environment definition](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/web/src/env.mjs#L679-L684). |
| Langfuse observation query | Default 50, maximum 1,000 observations per page | Cursor-paged rows of a selected representation, including queries for a single Trace. [Public API](https://langfuse.com/docs/api-and-data-platform/features/public-api). |
| Langfuse Trace UI topology | Default maximum 20,000 observations | Reads oldest-first and reports an explicit cutoff. This path is capped, not automatic traversal of every node in an arbitrarily large Trace. [Pinned cap](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/packages/shared/src/env.ts#L464-L474), [response contract](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/web/src/features/events/server/eventsRouter.ts#L584-L622). |
| LangSmith Trace ingestion | Maximum 25,000 runs per Trace; later runs rejected | Run count is separate from request size, field size and view limits. [Administration overview](https://docs.langchain.com/langsmith/administration-overview#maximum-runs-per-trace). |
| LangSmith query API v2 | Default 100, maximum 1,000 results per page | Explicit field projection and cursors. [Query migration](https://docs.langchain.com/langsmith/smithdb-sdk-migration-query-runs). |
| LangSmith Trace tree | Default at most 1,000 runs; max_runs above 1,000 rejected | Cloud update for October 5–12, 2026. This is a tree-read limit, not the ingestion limit. [Cloud changelog](https://docs.langchain.com/langsmith/changelog). |
| LangSmith message/trajectory processing | Default contribution budget 200 MB | Cloud update for September 14–21, 2026. A derived-view budget, not a universal Trace storage or browser-heap limit. [Cloud changelog](https://docs.langchain.com/langsmith/changelog). |

The inspected material does not specify one universal byte capacity covering every accepted Trace, every storage backend, and every complete-content read. Request limits, pagination counts and rendering guards must not be presented as that missing guarantee.

## Langfuse: compact projection and separate body access

The official architecture persists incoming raw events in S3, places references in Redis, and processes them asynchronously into ClickHouse. Compact derived data serves tables and dashboards; single-record access reaches full payloads. Deployments supporting separate read compute can also isolate query resources from ingestion. This is useful separation of durable ingestion assets, read representations and resource owners. It does not make ingestion acknowledgment equivalent to immediate query visibility. [Architecture](https://langfuse.com/resources/engineering/clickhouse-at-agent-scale).

The inspected materialized view derives `events_core` from `events_full`. It keeps 200 Unicode code points (using `leftUTF8`) of input, output and each metadata value; these are display/query projections, not a 200-byte limit or a mutation of the full row. A short value per key alone does not prove a fixed total row bound when keys and counts vary. [Pinned migration](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/packages/shared/clickhouse/migrations/canonical/0041_create_events_core_mv.up.sql#L39-L42).

The v4 UI path initially fetches observations without I/O, then requests the root's I/O separately; selected-observation detail likewise has separate body access. This is closer to a lightweight topology plus selected content than a whole-Trace JSON download. It still constructs a bounded node set and caps it; virtualization does not remove that materialization cost. [Pinned hook](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/web/src/features/events/hooks/useEventsTraceData.ts#L57-L61).

The public API defaults to `core,basic`; I/O, usage and metadata are separate field groups. I/O remains raw strings; current documentation rejects `parseIoAsJson=true`. Selected metadata keys can be expanded beyond their preview. [Public API](https://langfuse.com/docs/api-and-data-platform/features/public-api).

A particularly useful continuation contract was clarified on November 3, 2026: a short or empty page may still carry `meta.cursor`. Only the absence of continuation ends traversal. This supports bounded work without confusing progress with completeness; it is not, by itself, proof of exact response-byte budgeting or an as-of snapshot guarantee. [Continuation contract](https://langfuse.com/changelog/2026-11-03-observations-api-continuation).

Browser guards distinguish serialized length from structural work. The ordinary JSON view gates a single field above 2,000,000 UTF-16 code units; the JSON Beta path uses a lazy viewer at 3,333 expanded rows. Downloads remain an alternative. These are representation-specific render guards, not storage or measured browser-heap limits. [Pinned render policy](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/web/src/features/traces/components/IOPreview/fns/jsonViewSizeGate.ts#L22-L58).

Raw event retention also differs from full-record retention: the official blob guidance describes a 30-day Cloud raw-event lifecycle. Replay availability must therefore have its own retention contract. [Blob lifecycle](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage#bucket-lifecycle-policies).

## LangSmith: field projection, multipart and representation budgets

Current v2 run queries return only ID by default and require `selects` for other fields. Retrieval exposes preview fields separately from complete I/O. The nested child-run convenience surface is replaced by trace-run queries and parent-chain identifiers. These contracts favor explicit, bounded representations; they do not prove every proprietary UI path fetches children lazily. [Queries](https://docs.langchain.com/langsmith/smithdb-sdk-migration-query-runs), [retrieval and hierarchy](https://docs.langchain.com/langsmith/smithdb-sdk-migration-runs).

Multipart ingestion can carry out-of-band JSON fields and binary attachments, with attachments stored separately. This separates heavy content from the run envelope. It does not establish that one JSON value is chunked for range reads or that multipart alone isolates failures. [Multipart API](https://docs.langchain.com/langsmith/smith-api/runs/ingest-runs-multipart).

SDK batching also has different semantics from hard admission. Python's default threshold is 20,971,520 bytes (20 MiB), but the queue drain adds an item before checking the threshold and can send one oversized item. Its separate 20,000,000-byte threshold chooses inline versus streaming multipart bodies. [Constants](https://github.com/langchain-ai/langsmith-python/blob/afbf6b4d73d1de02d18021381bcb11a9966132b0/src/langsmith/_internal/_constants.py#L3-L10), [packing behavior](https://github.com/langchain-ai/langsmith-python/blob/afbf6b4d73d1de02d18021381bcb11a9966132b0/src/langsmith/_internal/_background_thread.py#L137-L177). JS uses a 24 MiB uncompressed batch target and also permits a large item to leave on its own. Neither value is a server per-run guarantee. [JS default](https://github.com/langchain-ai/langsmith-javascript/blob/8176ec8268094501c1cd3b02ca900b9b076d91d1/src/lib/client.ts#L901-L904), [JS packing](https://github.com/langchain-ai/langsmith-javascript/blob/8176ec8268094501c1cd3b02ca900b9b076d91d1/src/lib/client.ts#L1084-L1106).

Current self-hosted architecture must be versioned: SmithDB is an opt-in columnar datastore with durable object storage and disk caches, separate ingestion/query services and background compaction. ClickHouse remains required on 0.16 and the default on 0.17. Public documentation does not establish the exact HTTP success-to-durable-commit ordering needed by AI Vision's proposed acknowledgment contract. [SmithDB architecture](https://docs.langchain.com/langsmith/self-host-smithdb).

## Failure isolation: useful precedents with important gaps

LangSmith's June 29–July 3, 2026 Cloud update explicitly preserves multipart batches by replacing oversized inputs/outputs with placeholders. The notice does not publish the threshold or guarantee later recovery of the replaced originals. This is local degradation that preserves surrounding structure, not evidence of lossless accepted content or a guarantee for all fields and ingest protocols. [Cloud changelog](https://docs.langchain.com/langsmith/changelog).

Langfuse's inspected OTel processor rejects the entire export for malformed/missing Span IDs or malformed collection shapes. It explicitly logs when that also discards valid sibling Spans. This counterexample concerns validation, not a measured oversized-Span test, but rules out claiming universal per-item isolation. An envelope-level 413 is also request-level rejection. Legacy ingestion's per-event responses cannot establish current OTel or asynchronous database-write behavior. [Pinned validation path](https://github.com/langfuse/langfuse/blob/f257bef7c17b489ebd79a230e906abcc2d5ef5d7/web/src/server/otel/processOtelIngestion.ts#L109-L220).

No reviewed public contract proves automatic per-item recovery from every storage batch failure. Healthy sibling preservation under a deterministic oversized item remains a requirement AI Vision must implement and test itself.

## Recommended implications for AI Vision's proposed ADR

These are engineering recommendations, not claims about competitor internals or amendments already applied:

1. Keep the catalog/list independent of raw-body size and optional rich projection readiness. Queries must not hydrate an entire time window to prove that a small result page exists.
2. Deliver one closed read flow first: compact list, bounded Trace header/topology, then explicitly selected Span/field content. Reuse current storage initially; adopting S3, SmithDB or another analytics store is not a prerequisite.
3. Make item count, exact encoded response bytes, structural work and retained client ownership separate budgets. Derive product defaults from representative AI Vision workloads and Linux/browser measurements, not competitor constants.
4. Expose continuation, loaded ranges and capped/incomplete state. Preserve unknown business metrics; a capacity failure is neither business ERROR nor zero usage. An empty response with continuation is progress, not completion.
5. Prefer explicit rejection for new oversized content and truthful read failures over implicit placeholder substitution if accepted-content preservation remains the chosen policy. Verify mixed A/oversized-G/B inputs in different orders, rejected updates retaining prior values, retries, actual receipts and committed healthy rows.
6. Treat acknowledgment durability as an independent design decision. Direct database commit and a genuinely durable queue/object-store acceptance model can both support asynchronous query projection. The existing in-memory queue cannot acquire durable semantics merely by changing labels. Do not change the ADR's commit-before-ACK promise without an explicit reviewed decision.
7. Separate advanced snapshot history, complete content-chunk protocols and broad consumer migration into explicit delivery packages. Their guarantees must remain unavailable until implemented; a smaller first stage must not silently promise stable as-of pagination or arbitrary-size complete evidence.

The research supports the layered-read direction while suggesting a smaller first delivery. It does not justify weakening the user's isolation requirement or declaring the current numerical policy qualified.

## Fixed source snapshots

- Langfuse application: `f257bef7c17b489ebd79a230e906abcc2d5ef5d7` (package version 4.76.0).
- Langfuse docs: `b4bf843d3f1951539d39992ef8e91c455e915fde` for the pinned FAQ.
- LangSmith Python: `afbf6b4d73d1de02d18021381bcb11a9966132b0` (0.14.17).
- LangSmith JavaScript: `8176ec8268094501c1cd3b02ca900b9b076d91d1` (0.11.8).

Main-branch snapshots are not assertions about every deployed version. Context7 contained older behavior for I/O JSON parsing; current official Langfuse documentation was used instead. Throughput/rate limits are deliberately excluded from the per-item capacity comparison. The Agent Server Cloud 25MB deployment limit is not treated as LangSmith tracing capacity.

## Database-specific design follow-up

The [OceanBase execution profile](oceanbase-execution-profile-20261107.md) maps these observations to our actual transactional/indexed data foundation. Its selected baseline and qualified native snapshots/HTAP/governance replace any implication that the competitor physical storage architecture should be copied unchanged.
