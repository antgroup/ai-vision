---
object_key: trace-data-flow
topic: layered-capacity-isolation
title: Layered Trace Capacity and Failure Isolation
---

# Layered Trace Capacity and Failure Isolation

## trace-data-flow-layered-capacity-isolation@20261107-2148

**Keep accepted Trace data independent of online read/render capacity and isolate deterministic item failures.**

```yaml
record_id: trace-data-flow/layered-capacity-isolation@20261107-2148
decision_status: proposed
implementation_status: not_started
proposed_at: 2026-11-07T21:48+08:00
decided_at: null
implemented_at: null
supersedes:
  - trace-data-flow/layered-capacity-isolation@20261107-1540
related_records: []
affected_objects:
  - collection-gateway
  - native-trace-storage
  - trace-read-model
  - platform-trace-query
  - trace-presentation
  - trace-consumers
canonical_doc: ai-vision/docs/traces/query-performance-contract.md
```

`affected_objects` names the implementation boundaries of this root contract, not new entries in the AgentLens registry. The canonical document describes implemented behavior; this proposed ADR must not be copied there as an already available capability. The user selected layered reads and requested an overall design review before local implementation, regression tests and Linux screenshots. The precise defaults and tradeoffs below are reviewable engineering decisions, not previously accepted product limits or measured capacity guarantees.

This revision incorporates the requested Langfuse/LangSmith research and separates independently deliverable contracts. The [previous complete proposal](layered-capacity-isolation-20261107-1540.md) and its [fixed-hash review](reviews/layered-capacity-isolation-20261107.md) remain historical evidence; their review outcome does not approve this revision. The user's request approves incorporating the research direction, not every threshold, acknowledgment cutover or implementation described below.

### Background

Current limits protect different representations: native ingest has a request bound, database columns and packets have their own constraints, public upstream reads are bounded, a background worker has a larger transfer budget, and the UI limits rows or visible content. None is an end-to-end maximum Trace size. A Trace is a growing set of Span rows, often received over multiple requests.

Main `historical-source-revision-redacted` includes the targeted #155 worker-budget and #152 page-first compatibility fixes. The separate reviewed local capacity candidate isolates a known large-body Trace in the tested list/detail path; it is not all present in main. Remaining design defects are request-wide ingest transactions and early asynchronous ACK, whole-window core admission before Trace classification, aggregate page overflow without byte continuation, whole-Trace detail/Span reads, and browser-wide decode/layout/cache work. A few large Spans, many small Spans, deep/wide values and many normal Traces need separate boundaries. Passing one Linux synthetic fixture is not proof of all four.

Raw means accepted normalized telemetry, not the original incoming HTTP byte stream. Existing SDK truncation or historical mapper normalization cannot be reversed. No migration in this decision edits accepted historical raw values to satisfy a new display policy.

The [versioned research note](research/langfuse-langsmith-large-traces-20261107.md) distinguishes official contracts from fixed-source behavior. Langfuse's compact/full representations and metadata-first detail flow, and LangSmith's explicit field selection and separate topology limits, support this direction. Neither establishes universal lossless per-item isolation: Langfuse's inspected OTel validation can reject an entire export, while LangSmith's multipart oversized-I/O protection replaces fields with placeholders. Competitor limits are not qualification evidence for this policy. The [current implementation comparison](reviews/langfuse-langsmith-gap-20261107.md) separates main, local candidate and target capabilities.

### Decision

#### 1. Scope and invariants

1. **Within a valid, authenticated request envelope, a deterministic Span/Trace capacity violation MUST be local.** Healthy unrelated Traces commit or remain readable. A response represents rejected items explicitly; it never invents empty content, zero usage or business ERROR.
2. Request-level authentication, malformed/over-limit envelopes, unavailable infrastructure, unknown commit outcomes and aggregate overload are separate failures. An undecodable envelope cannot promise item outcomes. The supported gateway packs valid requests so an oversized item does not create an oversized downstream batch.
3. **Accepted storage, basic directory availability, rich projection readiness, complete content and renderability are separate states.** A large/dirty/unprojectable body must not remove a bounded catalog row or require sibling body hydration.
4. List cost in the application is proportional to the returned catalog page, not every Span in the physical window. No list/readiness proof may first download all window cores or bodies.
5. Pagination is bounded by both item count and exact encoded bytes. Planned continuation is not an item failure. A legal item that does not fit the remaining page appears first on the next page; it is not discarded or falsely marked oversized.
6. Every read rechecks current authorization. Source revision, catalog version and content proofs prevent mixed snapshots; tokens do not grant access. Business state is independent of read/admission state.
7. Preserve existing raw data. For **new** over-limit Spans use explicit rejection with receipt/coverage, not silent truncation. This record does not require unlimited admission, an object store, or a new producer multipart protocol. Such a protocol requires a later compatible decision.
8. Serialized byte counters are not heap/RSS guarantees. Count/depth/work limits, admission and actual resource measurements complement them. Browser OOM is not an exception boundary caught by React.

#### 1a. Delivery packages and guarantees

The following packages are independently reviewable. They separate delivery order from the complete target; a package is not permission to advertise guarantees it has not delivered.

| Package | Required result and boundary | Deferred dependency / qualification |
|---|---|---|
| R1: ordinary read/navigation | An independently available compact catalog, bounded header/topology and explicitly selected Span/field content. List work is page-bounded; no whole-window or whole-Trace hydration fallback. Use exact response-byte packing, count/work limits, current authorization, Trace revision proofs and explicit local refusal. | Reuse the current database and additive current catalog/index. Stable as-of history, arbitrary-size complete content and every consumer are not prerequisites. A selected body that cannot be read within the proved bound fails only that reference. |
| W1: admission and write isolation | Checked envelope/item limits, bounded byte packing, permanent item classification, independent Trace/chunk commits, rejected updates preserving old accepted values, and real healthy-row/retry assertions. Catalog tracking does not depend on optional rich maintenance. | Full content-chunk replication is not required. External durable ACK and terminal downstream receipts remain A1; W1 cannot make the existing asynchronous gateway claim them. |
| A1: external ACK/outcome profile | Review and qualify the commit-before-ACK profile in section4, actual HTTP/gRPC terminal receipts, sender timeout/retry/flush, loss after commit and backpressure. | Independent compatibility/cutover decision. A durable queue/object-store acceptance profile would require its own reviewed contract; a volatile queue is not an equivalent substitute. |
| J1: stable catalog history | Retained as-of catalog journal, epoch publication, expiry/cleanup and concurrency proof. | Enable only with explicit snapshot capability; R1 may use the declared live-keyset contract in section6. |
| C1: content/index expansion | Chunked original ranges, semantic descriptor/ordinal paging and complete bounded export units. | Add only when bounded access on existing storage is insufficient or the corresponding feature is required; measure storage amplification and engine work. |
| B1: browser multi-view/cache ownership | Shared retained/reserved accounting, pinned-view and cache ownership, LRU/expiry and bounded prefetch across views. | R1 starts with one active Trace pane, an8 MiB per-pane retained/reserved cap, no shared content cache or automatic content prefetch, and bounded local models/transport/interpretation. Opening multi-pane/pinned caching waits for B1. |
| X1: other consumers | Explicit Session, Labs, dashboard, export, CLI and diagnosis completeness/isolation contracts and evidence. | Migrate by consumer; unmigrated strict routes remain visibly limited and do not inherit R1 guarantees. |

The first implementation package is R1's closed list-to-selected-content flow; W1 can be developed separately. Calling the full ingest-to-browser outcome isolated requires W1, the actual external outcome path in A1 and the selected read/consumer coverage. R1 alone proves online read/navigation isolation only. The full record remains unimplemented until every claimed package and consumer is qualified.

Independent catalog availability is required in R1, not a later optimization. A giant body, rich-worker OFF or failed rich projection must not make its proven directory identity or healthy siblings disappear. Pending canonical fields remain unknown. A bodyless full-window scan is not a page-bounded catalog.

#### 2. Units and policy version

Publish one versioned policy, `trace-capacity-policy-v1`. The following units MUST be named in configuration, capability replies, metrics and tests:

| Unit | Definition |
|---|---|
| Wire envelope bytes | Bytes entering the transport before decompression; not HTTP header bytes |
| Expanded envelope bytes | Uncompressed protobuf/JSON message bytes before semantic object materialization |
| Canonical Span footprint | Actual serialized canonical one-Span OTLP protobuf envelope bytes including the full referenced resource and instrumentation scope |
| Normalized row bytes | Sum of actual UTF-8 bytes of normalized variable-width stored columns, including duplicated payload; no read-side allowance |
| Source estimate | Existing selected-column OCTET_LENGTH counters plus conservative framing/scope allowance; not disk usage or original OTLP size |
| Response bytes | Exact serialized UTF-8 success/error envelope after decompression, including JSON escaping/framing |
| Retained-accounted bytes | Encoded bytes plus documented conservative object charges retained by a client/server owner; not measured heap |
| Structural work | Span/node/edge/attribute/event/link counts, nesting depth and bounded worker duration |

`MiB=1048576 bytes`; `KiB=1024 bytes`. Byte, code-point and JavaScript UTF-16 length are never interchangeable. Runtime overrides must validate cross-layer relationships; lowering admission bounds does not rewrite existing data. Raising a hard bound requires boundary and resource qualification on supported platforms.

#### 3. Initial layered boundaries

The defaults are deliberately below the previous complete-body paths. They are starting policy values to qualify, not a throughput/SLA promise. Each row applies only when its package is implemented: section1a defines R1 versus later J1/C1/B1 profiles. Competitor request, Run-count or view limits do not establish these numbers; qualify representative few-large-Span, many-small-Span and deep/wide payloads at exact/cap+1 boundaries with Linux/browser resource measurements before a default cutover.

| Layer / configuration | Default / maximum | Result at boundary |
|---|---:|---|
| External HTTP envelope | 8 MiB wire AND 8 MiB expanded | Stream-count both; unknown/chunked length cannot bypass. Unusable envelope gets typed413, not fake per-item success. |
| External gRPC message | 8 MiB frame/message AND expanded | Explicit version change from old4 MiB default; structural preflight precedes expensive pdata allocation. |
| Compatibility ingress | Same8/8 MiB limits | Authenticate, then bounded decompression/normalization; no unbounded `gzip.decompress`. |
| Native internal envelope | 8 MiB, identity encoding only | Same counting/parser protections; preserve internal authentication. |
| Collector native export | Actual protobuf≤4 MiB AND≤256 Spans | Pack per input request with whole wrappers; reject one un-packable Span locally. No cross-request merge. |
| One new Span | Canonical footprint≤2 MiB; normalized row≤4 MiB | Both apply. Reject locally with specific permanent reason; no partial saved body. |
| Expanded-envelope structure |≤2048 Spans;≤128 resource groups;≤256 scope groups;≤32768 total key-value entries;≤8192 events and8192 links;≤65536 total AnyValue/value-container nodes; nesting≤32;≤262144 checked wire/token steps | Count all wrappers/nodes, including empty and unknown fields, before semantic allocation. Total structural overflow is an unusable-envelope error, not a fabricated per-Trace failure. Ordinary SDK/gateway packing stays below this envelope. |
| New Span structure | Span/resource/scope attrs≤256/128/64; events/links≤128 each; event/link attrs≤128; nesting≤32 | Count before full mapping/recursive materialization. A huge resource is charged to every referenced Span. |
| New scalar/value | Keys≤256 UTF-8 B; one compact canonical JSON UTF-8 attribute value≤1 MiB; name/schema≤512 code points; scope/correlation≤255; status text≤32 KiB UTF-8 | Exact presence/identity validation; reject rather than silently slice. Existing value/type semantics remain. |
| Ingest admission | 2 decoded input requests/process; 32 MiB aggregate reserved envelope buffers; wait≤100 ms | Refuse overload before accepting work. Keep process heap limiter as a final safeguard. |
| Native writes | One Trace at a time; transaction≤256 Spans AND≤16 MiB normalized rows; process native delivery concurrency2 | Split one input Trace into replay-safe bounded write chunks when necessary. A chunk never holds another Trace hostage. |
| One Trace accepted storage |≤128 MiB normalized current row bytes AND≤100000 accepted Span IDs | Enforce reversible counters under its source lock. Reject only new/increasing contributions beyond its quota; preserve accepted rows and record partial ingest coverage. Legacy over-quota raw is grandfathered, not rewritten. |
| L1 catalog item/page | Item≤8 KiB; page≤1 MiB; default20/max100 items | Optional over-limit metadata becomes explicit unavailable state; mandatory authorized identity remains exact. Byte-pack with continuation. |
| L2 Trace/Span header |≤128 KiB response | No long attributes, events, links or payload. Lists/reference counts replace large arrays. |
| L2 topology page |≤1 MiB; default200/max500 Span index records | No full Span bodies. Preserve unloaded-parent distinction and exact IDs/timestamps. |
| L3 content/descriptors | Default page target1 MiB; hard response2 MiB | Exact encoding/packing, bounded references and byte continuation; never construct an unbounded envelope first. |
| L3 raw fragment | Default128 KiB/max256 KiB source bytes | Align UTF-8 with≤3 extra boundary bytes; emitted range is exact. Shrink actual fragment to encoded page budget. |
| Content storage chunk |64 KiB binary payload | Finite DB parameters/rows; chunks and descriptors commit with their accepted raw revision. |
| New interactive upstream budget |4 MiB cumulative, also charged to existing64 MiB parent | Proof/hydration cannot reset it. New endpoint output limits are separately enforced on both server and browser. |
| Interactive server work | SQL/work deadline5 s; cleanup budget5 s; ordinary gateway upstream10 s; browser deadline15 s | Permit remains owned until query/transaction resources close; cleanup failure makes readiness unhealthy rather than returning a falsely free slot. |
| Read admission |2 active per Python process and2 active native layered reads/process | Admission is server-owned, not inferred from browser cancellation. Global capacity errors remain explicit. |
| Rich projection maintenance | Existing128 MiB source /192 MiB cumulative transfer initially; reducer scratch-accounting16 MiB,≤10000 nodes, work slice≤5 s | Large/unrepresentable rich job becomes item-local deferred; does not gate catalog or body references. Never advertise192 MiB as RSS. |
| Browser transport | Metadata1 MiB, header128 KiB, content2 MiB; error body8 KiB | Count decompressed stream before JSON parse. Oversized response aborts that request and is not cached/rendered. |
| Browser admission/retention |2 active; queue≤64; global retained/reserved≤24 MiB accounted, per-Trace≤8 MiB, cache≤40 entries | Include active-view pinned data, cache and in-flight reservations in one owner ledger; evict unpinned/expired/LRU data and release view ownership on close. A large Trace cannot consume the whole retained pool. Not a heap claim. |
| Browser topology/render |≤5000 resident index nodes; tree/timeline≤150 mounted rows; graph≤1000 nodes/2000 edges, layout worker≤1 s | Explicit loaded/subgraph/limit state; navigation and other Traces remain usable. |
| Browser structured value | Complete value≤256 KiB;≤10000 logical nodes; depth≤32; JSON mounted rows≤200 | Count/token preflight and bounded worker interpretation; otherwise text fragments with explicit original-range actions. |

One preflight work step is one decoded field/tag/token, not one UTF-8 byte; the independent8 MiB envelope already bounds byte scanning. Counts include empty wrappers and unknown-field work. Per-Span violations discovered inside a structurally valid envelope are local rejections; total envelope structural overflow is a request error because the receiver will not create an unbounded set of item objects.

The whole native request must not be expanded into an array of duplicated normalized rows before admission. Large shared resource/scope objects can multiply normalized storage. Validate/map/commit bounded Trace chunks and release them before processing the rest. A transaction byte stop is continuation within the request, not a permanent rejection of otherwise valid remaining Spans.

#### 4. Ingest outcome and ACK contract

A1 proposes a synchronous commit-before-ACK Trace profile. Switching the default gateway and removing its asynchronous cross-request `batch` and `sending_queue` are separate compatibility cutovers, not prerequisites for delivering R1. Keep SDK batching, authentication, authoritative Space stamping, process protection and bounded byte packing. Logs/metrics retain their own explicitly tested pipeline behavior; a Trace-only change must not silently alter them. Until A1 is qualified, describe the old gateway response as its actual acceptance/queue outcome and do not fabricate terminal downstream receipts.

Under the proposed A1 commit profile, gateway success follows native durable commit for every accepted Span and terminal permanent rejection for every rejected Span. No new WAL or durable queue is promised. Loss of a response after commit remains at-least-once retry, not an exactly-once transport guarantee. Reliable acceptance, index visibility and rich-view readiness remain separate states. A future durable asynchronous profile would need a genuinely persisted acceptance asset, replay/retention/outcome rules and separate approval; merely enlarging the current in-memory queue is not durable acknowledgment.

Native ingest validates predictable per-item capacity/field failures before writing. Write accepted bounded chunks in independent Trace transactions. Raw, source revision, mandatory current catalog/index and ingest-coverage facts commit atomically. Every additional advertised chunk/version/descriptor must share the accepted revision fence when C1/J1 is enabled; an unbuilt optional representation stays explicitly unavailable rather than blocking the basic catalog. Do not require all future sidecars before W1 can isolate its writes. A failed Trace transaction must not roll back a committed unrelated Trace. An unclassified SQL/driver/auth/schema/unknown-commit failure must not be relabeled as permanent item capacity.

Before packing or opening transactions, validate exact `(org,Space,TraceID,SpanID)` duplicates across the entire bounded input. Identical normalized versions are one persistence operation while every input occurrence remains acknowledged. If an ID has conflicting normalized versions in the same input, permanently reject all its occurrences with `conflicting_span_versions`; do not choose an arbitrary first/last value or let it straddle chunks. Independent IDs/Traces continue.

A request-owned typed receipt carries permanent rejection counts/reasons through the synchronous receiver/exporter pipeline. Native2xx partial success is terminal; HTTP and gRPC receiver responses MUST expose the real standard OTLP `partial_success.rejected_spans`. Bounded diagnostics include policy/reason/receipt IDs, not raw values or unbounded ID arrays. The current receiver-empty-response/exporter-log-only path must be replaced and tested on the actual wire; a YAML change alone is insufficient.

If a chunk has a retryable/unknown delivery outcome, return the appropriate non-success request result, not a false200 partial success. Stable IDs prevent duplicate stored rows. A replay matching the **current accepted normalized value** is a no-op, including source revision/count. Changed upserts replace old contributions and advance revision once per changed Trace transaction. This preserves the existing last-successful-arrival update policy; stable IDs alone do not provide causal ordering or revision-stable replay after an intervening changed update. A stale request after another update may itself be a changed upsert. Capacity-rejected occurrences are re-evaluated against then-current quota counters on a new delivery attempt; a later shrinking write, even in the same original input, can make a formerly rejected growth admissible on replay. Receipts describe each attempt, not a permanent remembered rejection. Stable-revision assertions apply only to the replayed accepted/current-matching set; changed acceptance legitimately advances revision. Producer sequence/idempotency keys would be a separate producer contract; this decision does not silently claim one. Strict admission rejects missing identities rather than generating new IDs during retry. Deterministic local rejections may be re-reported on a replay; counters distinguish attempts from unique accepted/rejected observations.

A rejected update never overwrites a previously accepted Span. Mark bounded ingest-coverage/rejection facts under the proven Trace scope without fabricating a Span or changing root business status. If all items are rejected and no existing Trace is authorized, a bounded receipt is enough; creating a fake readable Trace is not required.

Parsing and preflight are part of the boundary: a bounded wire buffer alone cannot prevent millions of tiny protobuf objects. Preflight repeated-field counts, nesting and value lengths before full semantic allocation. HTTP JSON uses a bounded token walk; protobuf/gRPC uses a checked codec/wire walk in the pinned receiver path. Unknown fields cannot bypass byte/work limits. Authentication precedes decompression wherever the transport permits it.

#### 5. Storage ownership and online catalog

Use additive sidecar tables. Do not add another historical raw-table STORED expression or rewrite accepted raw bodies. Keep the existing `trace_sources` identity/revision lock as the single source mutation fence; catalog tracking is independent of rich-worker maintenance ON/OFF.

| Sidecar | Role and invariant | First required package |
|---|---|---|
| `trace_catalog_v1` | One bounded current directory row per exact org/Space/Trace. Count/time/basic ownership, canonical field states, size/access/coverage and revision. Indexed discovery/filter/order never depends on a window of Span cores. | R1, with all writers fenced or readiness disabled |
| `span_index_v1` | Bodyless per-Span identity/parent/time/status/classification/root-update candidates; index by Trace/time/ID and Trace/parent/ID. No unbounded attributes/events/payload. Semantic component descriptors can follow in C1. | R1 topology, with per-Trace readiness |
| `span_content_chunks_v1` | Finite binary parts and descriptors, keyed by authorized scope/Span/content version/component/ordinal. Preserve bytes/hash/length; no mandatory object storage. | C1; absent capability is explicit |
| `trace_catalog_versions_v1` +scope epoch | Compact catalog-only versions for bounded stable list snapshots; no LOBs or all-Span facts. | J1 |
| Reversible owner/summary contributions | Maintain replacement-safe ownership and aggregate contributions; a sticky mixed-owner flag cannot represent a corrected upsert. | Correct ownership and proven canonical fields before publication; richer reducers may remain pending |

Current catalog/index changes belong to the raw-write fence. Future chunks/history are maintained atomically when their capability is enabled. R1 can use existing storage for a selected component only if size preflight and actual engine plans prove bounded extraction; otherwise return a reference-local capacity/unavailable result before full materialization. C1 chunk replication is not an obligatory first migration. No accepted full value is shortened into a preview or placeholder to make it readable. Dedup hashes cover every normalized writable value, with unambiguous NULL/type/length encoding, not only a subset of display fields. Content hash does not authorize access. All mutation paths, retention/import/reconciliation and writer-version cutovers maintain or explicitly invalidate this fence. Accepted storage quota counts current normalized data, not cumulative lifetime traffic, generated/index/chunk physical amplification or original OTLP bytes. Replacement subtracts the prior contribution before adding the new one; shrinking/correcting a grandfathered over-quota Trace is allowed when it does not increase its excess. Legacy unknown accounting closes admission for that Trace with a permanent, observable `legacy_trace_capacity_unverified` item result until reconciliation, rather than blocking unrelated Trace transactions or inventing zero bytes. Namespace retention/quota and physical disk exhaustion remain separately observable operator/infrastructure boundaries, not a promise of unlimited storage.

Canonical semantics are preserved: explicit root, parentless root, proven missing-parent candidate, then cycle fallback; stable start/ID order and multiple-root reasons; selected root's real OTel status; last-presence Trace name/SDK-user updates with explicit-null shadowing; existing environment/session/legacy-user precedence. A late parent or corrected upsert may change the result. Never choose the first loaded page row as root.

Use indexed contributions or a bounded bodyless canonical reducer with revision CAS. Until the exact result is proved, expose `canonicalState=pending|capacity_limited` and unknown affected fields. The basic authorized directory and independent selected-content access remain usable. The reducer may defer a many-Span Trace at its own budget; it must not first aggregate the whole physical window or stall sibling directories.

Layered access preserves existing whole-Trace ownership rules. New accepted input cannot add another Agent/subject/product ownership tuple to an already uniformly owned Trace; reject that incoming contribution without changing the previously accepted Trace. A new input group with conflicting ownership is locally rejected rather than selecting whichever Span arrived first. Never return another Agent's Span/body through a catalog keyed only by Space/Trace.

Legacy mixed/unknown ownership is quarantined until the same-revision full ownership proof is valid. An Agent/subject-specific list does not emit that Trace's ID, header, topology or content, and never authorizes the whole Trace from one matching contribution. It may mark discovery/membership unknown without exposing foreign candidates; only candidates independently proved within the caller's full requested authorization tuple can be named in unresolved results. Exact mixed/unknown-ownership access fails closed for that reference. Known valid siblings remain queryable; correcting legacy ownership uses an authorized reconciliation workflow, not a sticky boolean.

Known indexed predicates return confirmed rows. Authorized unresolved candidates have a separate bounded continuation and lower-bound/unknown counts; they are not silently treated as nonmatching. Unsupported arbitrary/content predicates explicitly decline or use a separately specified bounded search contract. They never trigger hidden full-window raw hydration in layered routes.

#### 6. List snapshots and per-Trace snapshots

R1 advertises `consistencyMode=live-keyset-v1`, with fixed authorized query/time bounds, deterministic keyset ordering and integrity-protected continuation. It does not claim an as-of snapshot: late arrivals and updates to ordering/filter fields may move results across pages. Return unknown totals by default and describe these live semantics in the UI; an exact stable traversal requires J1. A fixed time upper bound alone does not solve late updates. Do not use a global live watermark that invalidates every cursor on every write.

J1 advertises `consistencyMode=catalog-snapshot-v1` only after its own migration and concurrency qualification. The remaining as-of/history requirements in this section apply to J1, not to an unfinished R1. Use a compact catalog-version journal with **10-minute snapshot/cursor retention** and exact keyset order/tie breaks. Writers serialize scope epoch advancement with catalog publication; a snapshot epoch names committed catalog versions, not an uncommitted auto-increment high-water guess.

An as-of page selects only rows valid at its epoch and current authorized scope. It reads no raw bodies or all-window Span cores. Default `countMode=none` returns `totalItems:null`. Optional catalog-only count has a **500 ms maximum subdeadline**, additionally bounded by remaining request time minus100 ms serialization reserve. If that reserve is unavailable, skip the count. Finish the valid page read first; run the count in a separate read-only transaction at the same retained catalog epoch/scope so canceling its SQL cannot invalidate the page transaction. Expected count timeout/capacity gives unknown count, not loss of the already valid page. Both work and cleanup still obey enclosing5 s/5 s ownership deadlines. Snapshot history uses bounded cleanup chunks and separate storage/retention monitoring. Do not evict unexpired snapshot versions merely because a hot Trace changed; if storage admission is unavailable, report infrastructure overload rather than falsifying a snapshot. Expired cursors get explicit restart guidance, never partial new snapshots spliced into old pages.

Catalog history does not archive all historical raw content. Opening a Trace from an older list row obtains a fresh Trace header and labels a changed revision. Header/topology/content tokens then bind one current Trace revision. A change during that navigation yields a Trace-local snapshot conflict and at most one automatic restart; unrelated list/Trace views continue.

All public cursors/Trace snapshot tokens are opaque, versioned, integrity-protected and expire after10 minutes. Live and as-of modes are distinct signed cursor profiles and cannot be exchanged. The following catalog-history retention rules apply only to J1; live mode cannot mint a historical proof it does not retain. A continuation inherits the **original snapshot expiresAt**; signing a later page never renews it. Current catalog versions do not expire. Superseded versions are retained until at least their `validTo` commit time +10 minutes +30 seconds clock-skew margin; cleanup never uses `validFrom` or the new page issuance time as the retention cutoff. This interval retains every catalog version that an unexpired snapshot can reference. Per-Trace token renewals create a new snapshot explicitly and cannot extend an older catalog history lease. The platform signs the private data-plane continuation/proof with its existing stable Django signing secret and a dedicated protocol salt; replicas need the same configured secret. No new user-managed secret or browser-visible credential is required. The data plane independently validates every typed position, scope, generation, revision and budget; it never trusts cursor-supplied SQL or a raised limit.

Cursors are at most4 KiB and bind scope/query/order/time hash, consistency profile, schema/policy version, last represented position and expiry. J1 catalog cursors also bind their retained snapshot epoch; per-Trace cursors bind the Trace revision. R1 live catalog cursors bind their live scan position without inventing either a global revision or retained snapshot proof. Batch cursors bind the exact ordered deduplicated reference-selection hash; the client resends that same selection. A mismatch is a contract error. Re-authenticate every request, so token retention never preserves revoked access.

#### 7. Versioned layered read contract

Keep existing complete routes as explicit legacy strict contracts. Add public routes under the existing authenticated `/api/v1/spaces/{spaceId}/signoz` resolver and matching private data-plane routes. The public wrapper remains `{code,message,data}`.

| Route suffix | Representation / required behavior |
|---|---|
| `POST traces/catalog-page` | R1 `trace-catalog-v1`: summary/preview only, indexed live keyset; J1 separately adds retained as-of cursor. No detail/IO fallback |
| `GET trace/{traceId}/header` | `trace-header-v1`: bounded directory/root/count/coverage/availability and first-layer references |
| `GET trace/{traceId}/spans/page` | `span-index-page-v1`: bodyless topology page or bounded branch/metadata-search selection |
| `GET trace/{traceId}/span/{spanId}/header` | `span-header-v1`: metadata and independent component/event/attribute/link references |
| `GET trace/{traceId}/span/{spanId}/content/page` | R1 selects only a proved bounded original component/field or returns local refusal; C1 `span-content-page-v1` additionally supports chunked ranges, not full Trace hydration |
| `GET .../events/page`, `.../attributes/page`, `.../links/page` | C1 indexed descriptor pages and bounded value references, not repeated whole-LOB JSON parsing |
| `POST traces/content-page` | C1/X1:≤100 authorized references; independently byte-packed item outcomes +continuation |

Common protocol is `native-trace-layered-v1`. Capability replies name implemented packages and representation/consistency modes; the protocol name alone never advertises every route or arbitrary-size complete content. Request-level proof binds scope, policy/semantics version and the catalog snapshot where relevant. A **Trace-scoped result** additionally includes exact decimal-string `sourceRevision`, `catalogGeneration` and a Trace snapshot token. A mixed-Trace batch has no fictitious single source revision: each result/reference carries its own Trace/Span/content version and proof. A legitimate source change, expired Trace snapshot or pending index is an item-local outcome in that batch, and does not discard valid sibling results. Tampered request/cursor authorization or malformed selection remains a request error. Nanosecond integers remain decimal strings, not lossy JS numbers. `pageComplete`, `contentComplete`, `canonicalState`, `projectionState`, `membershipComplete`, producer finality and UI-loaded completeness are distinct. Do not reuse a bare `complete:true` for all of them.

A batch continuation keeps the original ordered-selection hash, including the originally supplied per-reference proofs. Do not replace a changed reference inside that selection. Emit its local stale/unavailable outcome once and advance; the UI may separately request a fresh header/reference for that Trace. Unchanged references continue in order.

Pages return `requestedLimit`, `returnedItems`, actual response-byte accounting, `hasMore`, `nextCursor`, `stopReason`, count mode/completeness and separate unresolved membership where relevant. A failed item consumes a bounded error record and advances once. A legal item not fitting the remaining envelope is deferred with continuation, not rejected. Guaranteed room for one bounded error item prevents no-progress loops. Exact total is not needed to navigate byte-packed pages. A short or empty page is not completion while `nextCursor`/`hasMore` indicates continuation. Work/time-limited empty pages carry a strictly advancing bounded scan position and an explicit stop reason; they cannot repeatedly return the same cursor. No client may silently stop on `returnedItems < requestedLimit`, or automatically traverse unlimited empty pages/prefetch work. Transport bytes, selected row count and server work each have their own limit.

A Span-index record is a separate type, not a cast of partial `OTelSpan`. It has exact ID/parent/time/status, bounded labels/classification states and parent-presence proof: `present_in_snapshot`, `missing_in_snapshot` or `unknown`. Local loaded state is separate. Unknown child counts are not0; off-page parents are not roots. Root/missing-parent diagnostic arrays are paged rather than unbounded header fields.

A body descriptor names source component/field role, representation, media type, encoding, length/hash, content version and availability. No arbitrary client SQL/JSONPath is accepted. JSON fragments are **original text ranges**, not independent JSON documents: preserve byte offsets, UTF-8 boundaries and an explicit partial state. Do not synthesize closing delimiters, parse each fragment, or call a preview original evidence. Copy displayed range and exact-original download are separate actions.

Semantic event/attribute/link paging requires a derived descriptor/ordinal index. If that index is not ready, return item-local `index_pending` and allowed raw-component access, or an explicit unavailable state. Repeated JSON_EXTRACT of a giant LONGTEXT on every page does not satisfy this contract.

**Budget lifetime is one HTTP request.** The new4 MiB layered upstream child budget and existing64 MiB parent are created at entry to the same platform HTTP request, charged together for every participating upstream response/proof/retry, and released at its close. No inner item loop, recursive fallback or automatic retry can reset them. The smaller child wins. A subsequent cursor page or an explicit UI content action is a new bounded HTTP request, with a new4/64 MiB pair; cursor state carries position/proof, not a hidden lifetime transfer credit. A retry within the same HTTP request spends the remaining budget. The at-most-one frontend snapshot restart is a new HTTP request, not a promise that all drawer activity shares64 MiB.

This does not allow automatic unbounded pagination: active-view prefetch is limited to one next page; resident topology/cache/render caps still apply across requests. There is no implicit "load every page/body" loop. Session/export use their separately bounded traversal/streaming contract and must not evade an operation limit by hiding recursive new HTTP requests.

#### 8. Projection is an optional richer view

The mandatory catalog/index comes from accepted writes and bounded backfill, not from success of whole-Trace rich projection. Reuse existing bounded summaries and previews, but make the basic directory independent of successful rich-row construction. Preview length is a display policy; full accepted content remains separately addressed and must not be replaced by an ingest placeholder. Rich rows retain canonical metadata, usage/coverage, statistics and bounded operation previews; they are not an unlimited original-content replica. A newer source revision immediately makes an older rich row ineligible as current.

Keep the existing192 MiB background transfer and128 MiB complete-source compatibility limits initially. Refactor maintenance to consume bounded index/content pages and bounded reducers, releasing chunks as it progresses. Optional rich work at its own scratch/node/time limit defers only that Trace. It does not reset request budgets, block the next Trace, or force a list to fetch raw bodies. `_contentComplete:false`, missing statistics, unknown tokens/cost and bounded display previews retain their existing truthful semantics.

The first delivery MUST NOT claim arbitrary-size exact rich aggregation. If a feature requires complete original evidence and cannot obtain it within its explicit materialization limit, it fails that Trace/task rather than using incomplete projections as evidence. All consumers choose their required completeness level.

#### 9. Frontend resource ownership

List navigation consumes catalog summaries and supplied previews only. Missing previews show unavailable/unknown; they never launch an implicit whole-Trace IO request. Opening a drawer requests a header and first topology page. Selecting a Span requests its descriptors/content only. Build process/search/graph models only for active bounded views.

Sparse topology retains exact parents and exposes loaded/context state. Tree/timeline virtualize visible rows and offer bounded load-more/ancestor/search operations. Graph is an explicitly labeled connected subgraph or a local layout-limit state. No omitted edge/node is called complete. Local search says loaded-metadata search; whole-Trace metadata search uses the server index. Complete business counts are never computed from a loaded subset.

Use a common bounded success/error stream reader for Trace-family and consuming dashboard requests. Count decompressed bytes before JSON.parse; Content-Length is only an early hint. On byte violation, cancel the reader, discard the result and cache nothing. Limit JSON/string interpretation and layout separately; partial/large content remains text ranges. External media is explicit and not automatically loaded by the bounded text view.

R1 starts with one active Trace pane, bounded endpoint streams and resident topology/JSON work, no shared content cache and no automatic content prefetch. Enforce a simple8 MiB per-pane retained/reserved accounting cap including loaded topology, content ranges, parsed view objects and in-flight maximum response reservations; this cap is required before B1. Reserve before fetching. Repeated manual content/page actions replace and release older ranges, or stop with a local capacity state if still-pinned values prevent a new reservation; they cannot accumulate unbounded text or parsed values in the pane. Retain only the current bounded catalog page and small bounded navigation identifiers, not every previously visited result page. Release prior-view owners and cancel pending content work on close/switch. Accounted bytes are not a heap measurement. This restricted profile is not a claim to support pinned or multiple retained views. B1 extends ownership across views/caches and must be qualified before those features or shared caching are enabled.

Under B1, the resource manager reserves the endpoint maximum before fetch, bounds queue/admission, transfers accounted ownership to active-view/cache owners, evicts unpinned expired/LRU entries, and purges on auth/scope changes. Active-view data stays charged while referenced even if its cache entry is evicted. Apply both the8 MiB per-Trace and24 MiB global accounted limits; whichever byte/node bound is reached first stops that Trace pane with an explicit local capacity state. Release prior-view ownership when switching/closing; preserve small navigation/header state so other Traces remain usable. It never holds duplicate complete raw/pretty/parsed Trace copies by default. Accounted bytes do not substitute for measured peak/retained heap.

Cancellation of queued work removes it. Active cancellation stops browser retention/parse/render and cancels bounded transport; it does not prove server work finished. Both platform/data plane own admission independently until their query/resources close. New requests may receive busy responses while old cleanup completes. Do not read unbounded discarded bodies to free a slot and do not release server permits solely because a socket closed. A failed cleanup deadline keeps the permit unavailable and makes readiness unhealthy; it does not allow unlimited replacement SQL work.

#### 10. Consumer contract and delivery scope

| Consumer | Required adaptation / explicit completeness policy |
|---|---|
| Ordinary Trace list/detail/Span UI | Use all three layers; no hidden full-detail/IO fallback. |
| Session list/detail and mixed IO batches | Directory/reference pages, per-Trace outcomes, resumable byte budgets; no accumulation of every Trace body before showing healthy rows. |
| Memory/MCP/Skill expanded calls | Canonical server aggregates remain authoritative; expanded content uses bounded item references. Unknown contributions stay unknown, ownership/dedup unchanged. |
| Framework dashboard | Keep server aggregate/operation pages, shared bounded transport/cache, truthful partial metrics; no detail N+1. |
| Browser/CLI raw export | Independent bounded streaming/produced-artifact path with per-item manifest; never assemble all Session bodies in browser memory. Legacy complete export remains explicitly strict until replacement. |
| Diagnostic tasks/evidence | Require complete original material for each selected Trace/task; refuse that item if its explicit materialization budget is exceeded. Selection/batches preserve other tasks. Direct SQL readers need their own preflight/bounded iteration and are not protected by Django middleware. |
| Complete evidence/materialization | Initial per-Trace/task ceiling8 MiB normalized source and5000 Spans, cumulative decoded transfer32 MiB, admission1/process; preflight before full object creation. Over-limit/incomplete evidence fails only that Trace/task and keeps other selected tasks running. Never diagnose a partial preview as complete evidence. |
| Bounded raw export unit |≤100 selected Trace references; per-Trace exact encoded export≤16 MiB and5000 Spans; unit≤32 MiB wire with8 KiB final-manifest reserve and30 s deadline. Pack whole eligible Trace exports where feasible, stream without browser Blob accumulation, and stop with explicit continuation before the next legal Trace that does not fit. Larger individual exports get local refusal or use the explicit legacy strict contract; no unlimited download promise. A midstream revision/error produces an incomplete per-Trace manifest entry, not a complete-original claim. |
| Legacy/Langfuse backends | Negotiate per-source support; label explicit legacy/limited mode. A SigNoz capability does not imply Langfuse supports the protocol. |

Full implementation status requires an explicit consumer coverage matrix and evidence. R1 covers ordinary Trace navigation only; X1 separately gates other consumers in this table. Unmigrated routes retain their explicit strict limits and must not be described as inheriting R1 isolation. A first vertical slice may be reviewed separately but is `partial`, not implementation of the whole ADR. For synchronous export, the input selection/cursor binds original references, each unit uses its own fixed request deadline/byte budget, and only explicit client continuation creates another unit. Export permission is checked at every unit; do not renew an expired catalog/Trace snapshot implicitly. A fresh selection is required after expiry. Later asynchronous export jobs require a separate bounded lease/artifact contract. No new path may silently revert to whole-window raw hydration to hide an unavailable capability.

### Unchanged

- Authoritative Space/Agent routing, current authorization, business status and normalized raw source semantics remain. Read limits do not become business failures or zero usage.
- Existing32 MiB upstream response /64 MiB cumulative complete-read limits and legacy strict API contracts remain until explicitly superseded. Background192 MiB transfer is not a public limit increase.
- No dependency-wide upgrade, producer multipart protocol, mandatory object store, unlimited ingestion or production capacity promise is part of this record.
- Existing AgentLens decision identities and diagnosis-specific completeness rules are not silently rewritten. Component decisions affected by implementation must be linked and reviewed by their owners.

### Impact and risks

**Deployment and migration.** Add sidecar tables without altering historical raw columns. Enroll directory identities from existing source tracking with bounded keyset batches; build one Trace's bodyless metadata/index/reducer at a time under byte/time/revision fences. Missing coverage is explicit. A giant legacy value that cannot be safely indexed remains accepted raw plus a local unavailable body state. Do not assume arbitrary SUBSTRING or JSON parsing has bounded database memory without engine evidence.

Catalog/basic coverage must progress past an unindexable Trace. Track historical enrollment and per-Trace index/derived states separately; a failed rich/content job cannot keep unrelated catalogs unready. No HTTP startup waits for an all-table raw rewrite. Reconciliation is resumable; progress/readiness is read back, not inferred from a task record or capability advertisement.

All writers must maintain source/catalog fences, including rich-worker OFF. Rollout needs a writer-version cutover and coverage proof. Old/out-of-band writers that bypass tracking invalidate readiness; an operator must fence/import/reconcile them before enabling layered guarantees. Advertised protocol support is separate from per-Space readiness.

**Versioned catalog costs (J1).** R1 live pagination deliberately makes no stable as-of promise. Ten-minute catalog history adds writes/storage and scope-epoch contention when J1 is enabled. Compact versions contain no LOBs, cleanup uses bounded transactions, and storage/growth/retention are observable. Capacity qualification includes hot-Trace updates and concurrent pagination. Journal expiry/rebuild is a declared restart boundary; ordinary writes by one Trace must not invalidate other unexpired list snapshots.

**Raw/content storage costs (C1).** R1 uses proved bounded reads on current storage or local refusal; full content replication is not required at first. Chunking may duplicate existing normalized raw values when C1 is enabled. Measure amplification, scope-local dedup and retention cleanup before widening budgets. A descriptor must never reference deleted/foreign/stale chunks. Whole-component hashes/lengths are verified without concatenating all chunks in the interactive process.

**Acknowledgment compatibility (A1).** R1 does not change the old gateway acknowledgment. The separately qualified synchronous profile changes latency/backpressure from queue acceptance to commit/terminal rejection. Qualify SDK timeout/retry behavior, throughput and duplicate updates. Preserve both HTTP/gRPC partial success on the actual wire; local consumer nil is not enough. No supported sender should lose healthy items merely because one rejected Span shared its batch.

**Rollback.** Disable new capability/default UI routes before rolling readers back; retain sidecars/raw and explicit legacy limits. Do not re-enable asynchronous acceptance under the claim of durable ACK. A write-contract downgrade needs an explicit operator mode that disables/invalidate catalog readiness before a legacy writer is admitted. No rollback deletes accepted raw or rewrites a newer catalog as valid old data.

### Alternatives

1. **Continue whole-Trace reads with larger budgets.** Smaller change, but preserves aggregate scans, batch poisoning and browser object amplification; rejected as the main direction.
2. **Add generated columns to the raw table for every new view.** Avoids some application maintenance but repeats historical rewrite/write-time JSON cost; rejected for this change.
3. **Mandatory external object storage or a new SDK multipart protocol.** Can support larger individual bodies but adds deployment/producer contracts; deferred. Explicit new-Span rejection plus preserved existing raw is the first policy.
4. **Layered catalog/index/content using the current database.** Selected direction; more versioned protocol/state than a threshold patch, but separates directory/healthy navigation from body and rich-view costs.
5. **Asynchronous gateway with a genuinely durable acceptance asset.** A separately reviewed alternative to A1 commit-before-ACK, with its own replay, retention, receipt/outcome and compatibility contract. Not adopted by this record and not required for R1; a larger volatile queue is not a substitute.

### Validation

The following gates are planned and MUST run against exact fixed sources before the respective implementation is claimed. Current earlier30 API/309 regression results do not cover this proposed contract. Every result names its delivery package and consistency/representation profile. R1 Linux acceptance does not qualify A1 durability, J1 stable history, C1 complete-range access, B1 shared ownership or X1 consumers.

| Gate | Meaningful negative/control cases and required evidence |
|---|---|
| Ingress preflight | Each HTTP/gRPC/compatibility protocol: exact/cap+1 wire+expanded bounds, compressed expansion, many tiny/empty protobuf objects, every total-wrapper/node/step boundary, deep JSON/AnyValue, malformed/truncated payload, huge shared resource, controls/multibyte and absent IDs. Verify checks precede expensive mapping. |
| Mixed permanent rejection | Good Trace A +over-limit Span/Trace G +good B, with G first/middle/last and both protocols/encodings. Verify actual rejected count/reasons, A/B durable rows, no rollback/drop, rejected update preserves old raw, no silent truncation. |
| Retry/unknown outcome | Failure before/after individual commits, lost responses, cancellation, deadlock/outage. Identical replay of the accepted/current-matching set preserves Span count, source revision and content identity without intervening mutations; include conflicting duplicate IDs across a256-item packing boundary. Exercise rejected growth followed by a shrinking write in later chunks: a new attempt may now accept that growth and produce a different receipt/revision; verify this explicit quota-reevaluation policy. With an intervening changed upsert, assert documented last-successful-arrival semantics rather than an unsupported causal-order guarantee. Partial success never hides unresolved transient outcome. |
| Catalog correctness | Duplicate/corrected upsert, late parent, explicit/null updates, cycle/multiple roots, mixed ownership then correction, tenant/Agent isolation, maintenance OFF, unsupported writer. Compare canonical gold; pending is never fake matched/nonmatched/zero. |
| Many-Span isolation | One Trace beyond old whole-window core/node budgets beside healthy siblings. Catalog pages/header/errors stay bounded, healthy rows/details available; no full-window Span-core/body SELECT in list query evidence. |
| Byte-packed pages | Many individually valid rows/bodies exceed one page. J1 stable continuation has no gaps/duplicates; R1 live tests expose concurrent-update semantics rather than promising a fixed snapshot. Include empty/short pages with an advancing continuation, repeated-cursor rejection and a final no-cursor page. In either mode, error items advance once, deferred healthy item returns later. No reset of4/64 MiB within an HTTP request; explicit next-page requests acquire fresh per-request budgets while resident/prefetch bounds persist; exact escaped envelope bounds. |
| Snapshot stability (J1) | A hot Trace mutates while another list spans pages; as-of directory pagination remains stable until its original expiry; repeated continuation does not renew it and cleanup retains a long-lived row superseded after snapshot creation. Trace-body revision conflict stays local. Revoke access mid-cursor; no leakage. |
| Body/index correctness (R1/C1) | R1 selected content is size-proved before materialization or fails locally while siblings stay usable; C1 adds exact chunk/hash/reassembly, UTF-8 boundaries, legacy invalid bytes, huge event/attribute counts, body-index pending, JSON fragments not parsed as documents, no repeated giant-LOB JSON_EXTRACT for descriptor pages. |
| Projection | Oversized/deferred Trace does not starve healthy worker/catalog jobs. Revision/context CAS, incomplete IO, unknown stats and fixed scratch/transport scopes. Original raw vectors unchanged. |
| Frontend boundaries (R1/B1) | R1 stream cap+1/large error/no Content-Length, one active pane, repeated manual content-page requests hitting the8 MiB retained/reserved cap, old-range replacement, current-page-only catalog retention, no shared content cache/prefetch, switch/close release, visible topology/JSON/graph limits; B1 adds cancel/queue65th/cache41st/per-Trace8 MiB andglobal24 MiB crossing/expiry/auth purge; pinned data remains charged after cache eviction and switching views releases it;5001 topology nodes; deep/wide JSON;1001-node/2001-edge graph; worker deadline and inactive-tab no-work. Measure actual DOM/heap/long tasks separately from counters. |
| Consumers | Trace, Session, Labs, dashboard, export, CLI and diagnosis each have declared completeness, per-item refusal and bounded continuation. A failure in one selected Trace must not abort unrelated work. |
| Linux acceptance by package | Fixed amd64 binaries and actual DB/backend/frontend; original protected resources unchanged. Normal middleware chain, giant legacy body, new rejected Span mixed with healthy Traces, many-small-Span Trace, deep/wide values, byte continuation, source-change/auth, export and consumer states. API/storage assertions plus real screenshots and interactions; no fixture/mock UI substitution. |

New boundary constants are tested at both exact and over-limit values; lowered synthetic limits are separate from default-limit Linux evidence. Resource qualification records cgroup/process/browser versions, peak/retained memory, main-thread work, query plans/returned bytes, retry/drop metrics and unverified scope. Screenshots prove visible state, not durability, resource upper bounds or cursor correctness alone.

### Implementation notes

This is the complete proposed resulting contract. Earlier local capacity fixes are a baseline and do not mark this ADR partially implemented. Preserve decision approval, implementation progress, fixed-source verification and production rollout as different facts.

Repository work is coordinated across `antcollector/`, the paired external SigNoz source, `ai-vision/aivision-py-be/`, `ai-vision/vfe/`, deployment compatibility and affected CLI/AgentLens consumers. Implementation is planned only after the overall ADR review; each slice links this exact record and lists remaining gates. No code, commit, push or merge is implied by authoring this ADR.

The first implementation plan is R1: current catalog/index readiness and writer fencing, compact list, header/bodyless topology, bounded selected content or item-local refusal, and a restricted single-pane browser flow. Run exact-source integration and Linux/API/storage/browser acceptance for that closed flow. Plan W1 admission/write isolation and A1 external outcome compatibility as separate linked packages; do not claim end-to-end permanent rejection until their actual wire path passes. J1, C1, B1 and each X1 consumer have explicit dependency and evidence gates rather than becoming prerequisites for the first useful read delivery. The complete target remains unchanged in strength for any capability once advertised. An implementation plan will provide files, interfaces and test commands after the revised target is approved.

References: [current query contract](../../../ai-vision/docs/traces/query-performance-contract.md), [gateway source/config](../../../antcollector/README.md), [platform guidelines](../../../ai-vision/AGENTS.md), [contribution evidence rules](../../../CONTRIBUTING.md), and [review guidelines](../../../REVIEW.md). Keep local machine paths, credentials, synthetic payloads, screenshot binaries and transient logs out of the ADR; retain separate evidence manifests linked from implementation records.
