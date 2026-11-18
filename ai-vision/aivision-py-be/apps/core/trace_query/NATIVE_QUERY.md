# Native query semantics

AI Vision trace lists, facets and metrics use the same canonical
`NativeTraceWindow` semantics. Supported trace lists use storage pagination and
project only the current page; compatibility reads and metrics use the complete
authorized, verified storage window. The metadata `queryCapabilities` is
generated from that query schema. Storage metadata is separately labelled and
does not promise that every physical column is publicly queryable.

- Time bounds are milliseconds or ISO UTC on input and use `[from, to)`.
  Omitted bounds freeze one 24-hour window for all pages and details.
- Public pages have at most 200 traces. Compatibility scans verify stable counts, unique IDs,
  complete span counts and a maximum of 10,000 traces. Incomplete data or an
  exceeded limit raises an error, rather than returning complete statistics.
- `status_code` follows the root observation's OTel status: ERROR when the
  root is ERROR, OK when the root is OK, otherwise UNSET. A failed child span
  (e.g. a retried tool call) stays visible on the span itself and does not
  fail the trace. Errors are counted per trace by the same root rule; the
  denominator is matched traces, and an empty denominator yields null.
- Explicit Langfuse trace name, IO, user and tags are read from every observation.
  Conflicts resolve per field by completion time (or start for unfinished spans),
  then start time and span ID, with the last value winning. Span timestamps are
  a deterministic proxy; the telemetry has no trace-update timestamp. Trace
  metadata merges keys in the same order. Root observation values are fallbacks
  only when the corresponding explicit trace value is absent; explicit empty,
  false, zero and null IO remain intact. This does not change collector resource
  identity, root selection or observation IO.
- Native `environment` is a trace-level field resolved only from the selected
  root. Nonblank string values use this precedence: root span
  `langfuse.environment`, `deployment.environment.name`, `deployment.environment`,
  then root resource `ant.agent.env`, `deployment.environment.name`,
  `deployment.environment`. Missing, non-string and whitespace-only values are
  skipped; when none is reported the value is `default`. Nonblank values retain
  their exact spelling and whitespace. `environmentSource` identifies the selected
  raw attribute path, or is null for the fallback; `rootObservationId` identifies
  its observation. Child observations resolve their own environment independently
  and do not supply a trace fallback. The native list row, detail `semantics`,
  `environment` filter, `env` shortcut and `facets.env` use this same root value.
  Raw attribute/resource fields retain their original values and any/all-observation
  predicate scope. Existing stored resource representations are resolved at read
  time. The public Langfuse v1 facade retains its existing environment projection.
- Canonical IO, usage summaries and environment are trace-level fields. Other
  observation/resource fields project the first non-null value, preferring the canonical root then
  stable observation order. Positive filters match any observation per
  predicate; negative filters (`!=`, `not_in`, `IS_NULL`) require all
  observations to satisfy the negative condition. Separate predicates may
  match different observations. This scope is included in field metadata.
- `keyword` searches the selected search field; `input_keyword` independently
  searches canonical input. Both conditions must match when present. Tags use
  OR semantics, matching the UI multiselect. JSON equality ignores object key
  order. Unknown fields, operators and parameters fail explicitly.
- Metrics aggregate all matched traces, independent of list pagination. Model
  or provider filters select traces; groups include all billable observations
  in those traces. Grouped and time-bucket usage retain the full ancestry so
  ambiguous nested generation totals remain unknown across groups.
- Canonical usage and costs come from `semantics/usage.py`. Null is unknown,
  zero is a reported zero. Coverage describes reported measurements and is not
  a provider invoice. Missing SDK telemetry cannot be inferred from receipts.
  A native `model_routing` or `tool_approval` decision is classified as a
  `GENERATION` call only when its span type is generic `SPAN`/`DECISION`, a
  nonempty model is available, and normalized input, output or total usage is
  reported (including zero). `agentic.decision.model` is a fallback after the
  standard/Langfuse model fields. Policy-only decisions, ordinary spans and
  explicit Agent/Tool rollups retain their type; raw attributes are unchanged.
- Native trace list rows and detail `semantics` expose `usage`, `reportedUsage`
  and `coverage` from the same normalized observations. `usage.total` and the
  compatibility `total_tokens` field remain null when any call is missing usage
  or nested calls have ambiguous accounting; `usage.unknownReason` explains why.
  `reportedUsage.total` is `{value: number | null, reportedCalls: integer}`.
  `coverage.billableCalls` counts unique `GENERATION` and `EMBEDDING` calls;
  `coverage.knownUsageCalls` is the count reporting total tokens. A 10/11 coverage
  with 120 reported tokens therefore remains an unknown canonical total with a
  subtotal of 120. Coverage describes the observed calls, not trace capture
  completeness. All-unknown measurements have a null subtotal; an explicit zero
  stays zero. Independent retries retain their identities, duplicate exports are
  deduplicated and cancelled calls receive no estimated usage. List summaries
  use already fetched page/window observations without per-row detail requests.
  These additive native fields do not change the public Langfuse v1 response.
- Native summaries and metrics expose `reportedUsage`, the **known usage subtotal** for
  each of `input`, `output` and `total`: `{value: number | null, reportedCalls:
  integer}`. Calls are deduplicated by `(traceId, observation id)`; each field
  sums only its available normalized measurements and counts those calls.
  No measurements yields null, while a reported zero stays zero. These use the
  existing inclusive input/cache semantics, including a total derived from
  reported input and output on the same call; missing values are not inferred.
- Metrics `usageByType` contains only present `GENERATION` and `EMBEDDING` types,
  in that order. Each item has `type`, a deduplicated `callCount`, canonical
  `usage`, and `reportedUsage`. Canonical usage keeps the full matched-window
  ancestry, including ancestors of a different type. An embedding's absent
  output-token measurement remains null; vector output does not imply zero
  output tokens. Both new fields describe the filtered complete window and do
  not change existing `usage`, `coverage`, V1, or Session response contracts.
  Known subtotals can include overlapping reports from nested calls. When
  canonical `usage.unknownReason` is `ambiguous_nested_generations`, consumers
  must not use `reportedUsage` as a main-card total, even at full coverage; it
  remains diagnostic reported data rather than a deduplicated token total.
- Native session lists use `NativeSessionWindow` to select the Agent UUID,
  filter raw spans, derive session metadata/facets and paginate locally.
  Every successful page, including an empty page, requires a verified scan of
  the authorized physical Space/product window. The 10,000-trace limit applies
  before local UUID selection; incomplete data or an exceeded limit fails.
  When a UUID is selected, every physical trace must have one nonempty Agent
  UUID shared by all its spans. Missing or mixed identities fail the scan;
  consistent traces from other Agents are excluded locally.
  Native pages default to 50 sessions, allow at most 200 and sort by descending
  `last_activity`. All predicates must match the same raw span to select session
  matches and their metadata. Session identity follows canonical observation
  precedence (`gen_ai.conversation.id`, `langfuse.session.id`, `session.id`, then
  the storage `session_id` fallback), matching session usage reads.
  `trace_count` and `span_count` describe that
  matching subset. Environment, scene and username use the maximum non-null
  value among matching spans. Session timestamp fields contain RFC3339Nano
  text, including the historical names `start_time_unix_nano` and
  `end_time_unix_nano`; an absent end remains null. Session facets count distinct
  sessions and omit their own dimension's filters. Valid `fields` are accepted
  without projecting the fixed row shape. Canonical `usage`, `coverage` and
  nullable compatibility
  fields `input_tokens`, `output_tokens`, `total_tokens` are always present.
  Totals use the same `aggregate_usage` rules as session usage queries: unique
  billable calls, inclusive input/output and full observation ancestry. They
  include all traces associated with the selected session within the frozen
  `[from, to)` and Agent UUID scope, regardless of list filters. List metadata
  records the exact frozen `from`, `to`, `timeBoundary`, `agent_uuid` and
  `agent_product`; `complete` confirms the verified window.
  `coverage.knownTokens` is a reported subtotal. Even when
  `knownUsageCalls` equals `billableCalls`,
  `usage.unknownReason = ambiguous_nested_generations` keeps totals unknown.

Trace facets count distinct matching traces, not span rows. Session IO includes the
selected trace/span provenance and completion state. The versioned JSON export
retains nanosecond timestamps as strings and records query scope and
completeness; it is a native-span representation, not an OTLP wire payload.

## Skill observability window

MCP and memory scans request `/api/v1/ai-vision/mcp/tools` with
`spans_only=true`. Compatible SigNoz versions omit the unused tool summaries
(`data` is empty and its `meta` counts are zero) and return the usual
`spans` and `spanMeta`.
Older versions ignore this optional parameter and retain the same scan results,
but still pay for the unused summaries. Deploy the matching SigNoz optimization
to reduce first-load latency.

The optimized MCP and Skill span pages compute their total with
`COUNT(*) OVER ()` in the page query, so the predicate and count share one
statement. Empty pages beyond page one use a count query to preserve pagination
metadata. Ordering includes timestamp, trace ID and span ID to keep ties stable.
Skill argument keys are extracted together to avoid repeatedly parsing the same
attribute document.

The Skill overview requires SigNoz's `GET /api/v1/ai-vision/skill/spans`
endpoint, introduced in [ob-labs/signoz#10](https://github.com/ob-labs/signoz/pull/10).
Deploy the compatible SigNoz implementation before or alongside this feature.
An older SigNoz without the endpoint produces an explicit scan error; it does
not establish that the selected window has no Skill calls. Trace-detail Skill
cards use the existing complete trace response independently of this endpoint.

Storage recalls native Skill attributes and candidate argument payloads. Python
then verifies read evidence and aggregates every declared span page, up to
10,000 candidates. Missing or inconsistent pagination metadata, duplicate IDs,
changed totals, and incomplete pages fail the scan. Narrow the time range when
the candidate limit is exceeded. These checks do not provide a storage snapshot
across page requests; concurrent replacement with unchanged counts remains a
limitation. Explicit `agentic.tool.derived` values remain distinct from direct
Skill reports.

`relations` contains only ancestor links established by the recalled candidates.
An ordinary intermediate span is not recalled by this endpoint, so its missing
ancestry cannot establish a Skill relation. The current Skill UI does not render
a relation graph; this field must not be described as a complete dependency graph.

## Storage trace pagination

With a SigNoz version exposing `GET /api/v1/ai-vision/traces/page`, supported
lists issue one upstream request regardless of the number of traces in the
window. SigNoz selects whole traces, counts the filtered result, computes facets
and returns complete spans for the current page in a single SQL statement.
AI Vision verifies the versioned `native-trace-page-v1` contract, scope, frozen
bounds, page length, span counts, unique IDs and native predicate/order agreement
before projecting that page. Canonical usage and IO still use the existing
Python semantics. A page must never be interpreted as a full-window metric.

- `meta.queryMode = storage` identifies this path. It has no 10,000-trace scan
  limit; storage time and result-size limits still apply. `meta.complete` proves
  a complete page and same-statement totals/facets, not that the page contains
  the entire window. Separate page requests can observe later ingestion.
- Supported sorts/filters are trace ID, start/end nanoseconds, duration,
  status and span count. Raw environment attributes, scene, username and Agent
  UUID predicates retain the any/all-observation rules above. Trace ID substring
  search and canonical string tags use storage filtering. Returned facets
  describe the complete filtered set, including each facet's own filter.
- Canonical root-environment facets require the storage metadata capability
  `environmentSemantics = root-span-resource-v1`. Without it, ordinary lists keep
  fast storage pagination but omit `facets.env` and report
  `meta.environmentFacetsComplete = false`; they never substitute raw resource
  counts or current-page counts. The UI requests canonical environment facets
  lazily when opening the Environment filter, through the existing facets
  endpoint with the same frozen bounds, Agent identity and list predicates.
  That verified complete-window request retains the 10,000-trace cap and its
  explicit errors. This does not add per-row usage/detail requests.
- An `env` shortcut response from a storage server without that capability uses
  the verified compatibility scan, including when the old storage page is empty.
  A declared compatible server can process `env` and return canonical facets
  on the fast path. Structured `environment` filters and sorts currently use the
  verified compatibility scan. Full-window native list/facets responses report
  `environmentFacetsComplete = true` and the same environment semantics version.
  This repository does not upgrade SigNoz's storage implementation.
- Storage validates span Space identity and, when an Agent UUID is selected,
  consistent nonempty Agent identity across the authorized physical window,
  including traces that do not match the selected page. Observation filters
  select trace IDs and never discard spans used by canonical usage.
- Token filters/sorts, canonical content/name/session searches, dynamic fields,
  diagnosis-input selection and unsupported dimension/tag representations use
  the existing verified scan, labelled `meta.queryMode = compatibility_scan`.
  Standalone metrics, facets and Session APIs retain their complete-window path.
- A missing endpoint (HTTP 404) or an explicit unsupported-capability response
  permits compatibility fallback. Transport failures, invalid successful
  responses and incomplete/scope-invalid pages fail; they must not silently
  trigger a full scan or be reported as empty success.

Roll out the SigNoz endpoint before or alongside AI Vision. Until SigNoz is
upgraded the existing scan remains available. The UI's separate current-page
IO request and on-demand detail requests retain their existing contracts.

The native Trace-list fallback may opt into display-only IO by posting
`preview_only: "1"` to `/api/v1/spaces/{space_id}/signoz/traces/io`. This reads
compact media without restoring original bodies and removes media references
before truncation. Each nonempty `input`/`output` is a valid JSON
`message-content-v1` display string of at most 1,024 characters; explicit null and
empty strings remain null and empty. Rows return `id`, bounded `input`/`output`
and `ioPreview` metadata, without full `inputValue`/`outputValue` copies. These
previews must not be used for copy, export or content search. Display previews
select the complete last message before extracting ordered text parts; media
references are omitted, while literal JSON strings in text remain unchanged.
Caption-only preview semantics participate in the projection context so older
persisted previews are rebuilt through bounded managed projection. Omitting the
opt-in preserves the complete canonical IO and typed-value contract for existing
consumers.

New native share links sign the selected `agent_uuid` together with the Space,
product and time bounds. If no UUID is supplied, exactly one active Agent must
match; its UUID is frozen into the link. Ambiguous selections fail explicitly.
Default `lf` requests that resolve to native storage follow the same rule and
sign the resolved native protocol, so later route changes cannot widen a link.
Public session IO, usage, trace drilldowns and observations reuse that signed
scope and ignore unsigned viewer selectors. Existing version-1 links without
an Agent UUID retain their original product-level scope; recreate them to bind
an individual Agent instance.
