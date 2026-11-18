# Native Trace and Session queries: operation and recovery

This reference applies to matching AI Vision and SigNoz source builds containing managed projection maintenance. Older released images may not contain these commands or contracts. External Langfuse retains its own query path. Raw Spans remain the source of truth; derived summaries can be rebuilt.

## Two emergency controls

| Process | Setting | Default | Effect |
|---|---|---|---|
| AI Vision backend | `AIVISION_QUERY_MODE` | `auto` | `auto` uses verified indexed pages, then a complete hybrid/raw snapshot when needed. `raw` bypasses derived summaries for Trace lists, canonical Session lists and their facets. Filters retain the same meaning. |
| SigNoz | `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED` | `true` | `false` durably suspends derived maintenance and serving across instances before untracked raw writes. Returning the process setting to `true` does not clear that suspension; explicit recovery is required. |

Environment changes require restarting the affected process. The Docker image blueprint, source blueprint and Kubernetes manifests forward both controls. Bounded list previews, batched Session summaries and Token query acceleration are standard implementation paths; they have no separate switches. Retired Trace/Session `legacy`/`shadow`/`prefer` and individual SQL strategy variables no longer select runtime behavior.

Deployment configuration is owned by [`deploy/`](../../../deploy/README.md). CLI vars and product Compose `.env` prefer `BACKEND_QUERY_MODE` and `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED`; they map to the two application environment names above. These are aliases, not additional controls. Edit canonical `deploy/` inputs and run `make assets` in `aivision-cli/` to update embedded and Compose outputs. Reuse the original configuration, namespace and lifecycle entrypoint when applying changes.

## Automatic schema and maintenance

SigNoz creates the derived control/source/projection/coverage tables and applies a serialized, versioned migration. The standard projection schema includes `session_trace_tokens_v1` (`DECIMAL(65,0)`) and `session_billable_v1` (`TINYINT`) STORED generated columns. Both derive from `row_json`, including writes by older publishers. They preserve the existing SQL CASE and numeric conversion rules; they do not guarantee arbitrary-size integer precision.

A populated-table migration can consume write and storage resources. It uses a dedicated bounded migration connection; ordinary query deadlines and global database settings remain unchanged. A failure keeps derived reads ineligible and is retried. Raw ingestion and raw snapshot recovery remain available when their own schema and connection are healthy. A real permission, transport or malformed-response error is surfaced; it is never represented as an empty result.

The first managed migration rebuilds untrusted historical derived state, retaining raw Spans. Derived cleanup commits at most 256 rows per transaction, so raw writers can proceed between batches; an interrupted attempt retains OFF and resumes from remaining records. Explicit operator recovery has a four-minute server deadline and a 245-second client deadline; the client automatically sends the existing `timeout: 240` request header so the main API timeout middleware honors that operation budget. Ordinary query deadlines are unchanged. These deadlines apply to one resume HTTP request. The management command also performs one bounded projection iteration, so its total wall time can be longer; the supervised worker continues catch-up afterward. An ordinary restart at the current schema version preserves coverage and historical enrollment cursors. Multiple instances coordinate the migration lease and shared maintenance fence. A failed or interrupted migration can resume from the observed schema.

The normal backend entrypoint supervises the production Gunicorn API and `maintain_trace_read_model` together; explicit bootstrap commands bypass this server supervisor. The worker discovers live Agents, Spaces and enabled native trace routes, enrolls historical data, and refreshes dirty or context-stale Traces. New routes and credential/price-context changes are discovered without a per-Space process. Publication checks the current source revision; conflicting updates remain pending. Bounded snapshot/publication batches reduce transport overhead while retaining per-Trace revision checks.

For a source-run web server outside the normal entrypoint, run the worker under the same backend configuration:

```sh
python manage.py maintain_trace_read_model
```

`--once` runs one discovery iteration; `--limit` bounds Traces per route. The output reports enrollment, publication, deferred sources and conflicts. Historical enrollment completion does not by itself prove every summary is ready. A failed route retries without blocking later routes. The existing per-Space `project_trace_read_model` command remains a diagnostic tool.

## Reading while projections catch up

Framework dashboards use the same canonical Trace/Session queries as the Agent Trace page. Session tables use server pages of 10, 20 or 50 items; opening a Session retains that canonical grouping. Operation trends can use the typed SQL aggregation below. General metrics use versioned compact observation facts while the `statistics-v1` aggregate lacks complete cost provenance. The facts retain call identity, ancestry, timestamps, type, model/provider and normalized usage/cost, so nested-call ambiguity and unknown usage keep their raw-query meaning.

Clean summaries in the dedicated metrics window exclude message bodies and IO previews; dirty or unavailable summaries retain complete raw recovery. Content predicates retain the complete-content path. Concurrent fallback sections with the same storage credentials, physical scope and frozen window share only an active snapshot read; completed snapshots are not cached. A read after completion executes again; concurrent refreshes of the same frozen window may join the active read. The event stream requests at most 200 latest operation keys and hydrates only their selected Traces, in batches of at most 100 complete source snapshots. Both source revision and maintenance generation must match the key selection; any change abandons the entire selection and rereads one complete raw window. Older compact windows without a generation proof use that safe raw recovery for split-step hydration. Atomic metrics/list queries and raw-only reads do not acquire a new derived-state dependency.

The additional facts keep the existing canonical/price projection context, so ready Trace and Session pages stay eligible during backfill. Matching data planes accept the worker's optional `required_supplement` predicate and refresh summaries missing the requested facts in bounded batches. Populating this JSON supplement does not require a table migration or switch; the optional typed statistics schema below is migrated separately. `supplementSupported=false` in worker output identifies an older data plane: ordinary dirty maintenance continues, but historical facts are not proactively added until the data plane is upgraded. During catch-up, dashboards missing facts select a complete raw snapshot; authentication, transport and malformed data remain errors. `AIVISION_QUERY_MODE=raw` also bypasses the compact dashboard path. Existing deployment aliases and the two emergency controls are unchanged.

A ready page uses SQL grouping/filtering over canonical summaries. A dirty, unprojectable or boundary-crossing Trace does not require fetching every Trace separately. The fallback reads trustworthy summaries and the remaining authorized raw spans in one repeatable-read transaction. Python applies the same canonical semantics to the merged window. Raw mode reads the complete authorized window without consulting projections. Recovery preflights summary bytes and selects the complete raw window in the same transaction if summary transport exceeds the response budget. It retains the same limits and fails explicitly if raw recovery also exceeds them.

The recovery contract supports at most 50,000 Traces and 500,000 raw Spans, with 512 MiB of selected source fields, 16 MiB per Span, and a 256 MiB compact response. Duplicated transport copies are removed without truncating canonical IO or attributes; rows are checked while streaming. An exceeded resource bound fails explicitly and asks for a narrower range. It never returns a truncated successful list. This covers the 30,000-Trace test window underlying 10,000 Sessions; it is not an unbounded scan or a production latency guarantee. Older data planes lacking the snapshot capability retain their existing bounded compatibility path.

Ready Session pages retain limits of 50 Sessions, 5,000 selected Traces and 16 MiB. Exceeding these ready-page bounds selects complete snapshot recovery. List previews contain at most 1,024 Unicode characters per serialized IO field; content search and detail/export use the complete recorded canonical content.

## OceanBase statistics aggregation

The current `statistics-v1` contract contains amounts and counts but cannot reconstruct `costCoverage` pricing sources, unknown reasons, subscription markers or list-price equivalents. After validating that response, general metrics fall back to the complete compact metrics window; `framework.events.timeseries` still uses verified typed operation counts, and recent-operation paging still hydrates revision-bound content. Compact facts retain canonical cost provenance and use the same request-local Space prices and projection context as raw fallback. Previously measured typed-metrics timings describe their recorded source only; they are not performance results for this pricing-compatible integration.

Matching data planes can serve common complete-window statistics from separate typed Trace and operation contribution tables. New tables default to explicit row storage; existing hybrid/column layouts remain usable without automatic conversion. Windows containing both a missing and an explicitly empty grouping key retain the existing window to preserve canonical group ordering. SQL returns counts, usage accumulators, model/provider groups and time buckets, plus a bounded latency-only vector for the existing exact latency calculation. Message bodies, previews and per-observation JSON are not part of this read. Content and other unsupported predicates retain the verified complete-window path.

The optional schema uses a separate additive migration lease and does not change base projection readiness, generation or canonical/price context. A STORED generated receipt on the existing projection document is recomputed on every write, including old publishers, so typed contributions cannot remain eligible after their source document changes. The normal projector incrementally populates missing contributions and atomically replaces a Trace's previous contributions under the existing source revision and maintenance fence. Unsupported dimension/numeric values and facts exceeding the publication budget keep ordinary summaries usable and record an ineligible receipt; they do not trigger continuous reprojection of an unchanged document. The automatic path targets OceanBase 4.4 or later, including CE and Enterprise; older engines keep the existing compact-window recovery. Actual SQL/layout validation in this change is on CE 4.4.2.1. The aggregation uses one combined completeness/Trace-summary query, a bounded latency query and one `GROUPING SETS` query for the five usage/operation partitions.

Dirty, stale, missing or boundary-crossing contributions select the existing metrics-window/raw recovery. Unknown Token usage, nested-call ambiguity, reported subtotals, coverage and Decimal cost retain their canonical meaning. Recovery clears optional contribution tables in the same bounded, fenced chunks as the base model; missing optional tables are allowed. No additional environment switch is introduced: `AIVISION_QUERY_MODE=raw` bypasses this path and the existing maintenance OFF latch remains authoritative. Storage-layout measurements and local validation boundaries are recorded separately from production-capacity claims.

See the [OceanBase aggregation verification](../../testing/ob-ap-dashboard-aggregation-20261026.md) for the same-data four-framework/10k results, row/hybrid/column comparison, additional projection-write cost and concurrent-list contention. Those prepared local API measurements are not a production capacity or browser-render guarantee.

### Lightweight seekdb compatibility

OceanBase CE remains the default recommendation for team deployments and growing observability workloads. seekdb is the lightweight option for individual use and local development. Recognized seekdb 1.x versions from 1.4 can reuse the typed row tables, incremental maintenance and bounded operation-page/content path. Its tables use ordinary row-table DDL without OceanBase's explicit column-group clause; its aggregate uses explicit grouping axes with `CASE` and `GROUP BY`, rather than requiring `GROUPING SETS`. Both shapes return the same canonical contract and retain the existing completeness, source-revision and maintenance-generation checks.

The query shape is selected internally; there is no additional user switch. Older/unrecognized engines and unavailable optional facts retain complete-window recovery. The [seekdb compatibility verification](../../testing/seekdb-dashboard-compatibility-20261026.md) records the actual tested version, data, costs and limits separately from the OceanBase measurements; it is not a guarantee for the default personal-use resource settings.

### Upgrade expectations

Upgrade the matching SigNoz data backend and AI Vision Python backend. On supported OceanBase 4.4+ and recognized seekdb 1.x deployments from 1.4, startup automatically creates `<prefix>_trace_statistics` and `<prefix>_call_statistics` (including its time index), and adds the `statistics_receipt_v1` STORED generated column to `<prefix>_trace_projections`. The existing database account needs `CREATE`, `ALTER`, ordinary read/write permissions and schema-metadata access. A normal upgrade needs no hand-written SQL or raw Span table rewrite. Older/unrecognized engines keep the existing query path without this optional schema.

The normal worker populates historical contributions incrementally. Missing or ineligible facts use complete-window recovery while ready Trace/Session pages retain their base readiness. Adding a STORED column to a populated table can consume IO/storage and encounter DDL locking; allow an appropriate upgrade window. The 12-Trace initializer test does not establish production-history migration time. Optional initialization failures are logged and retried; absent/incompatible optional facts permit recovery, but real metadata-permission, connection or SQL errors remain visible errors. Use the existing raw query control for functional rollback; dropping these columns or tables is not required.

## Filter semantics and evidence

Task user, environment, tags and input/output search must match the same Trace. Matching Sessions retain all selected-window turns for counts, usage, status, duration and expansion. Missing Session IDs do not create invented Sessions. Environment and SDK task user remain separate from physical authorization identity.

Total Token thresholds, including zero, require known billable usage. Missing usage is not zero. Tool-only Traces do not invalidate known billable usage from other turns. Stored columns are used only when their definitions match; absent or mismatched columns select the internal JSON_TABLE compatibility strategy. Metadata inspection and database errors remain errors.

| Response evidence | Meaning |
|---|---|
| `meta.queryMode=read_model` | Verified indexed page. |
| `meta.queryMode=hybrid_snapshot` | Complete snapshot combines clean summaries and raw data. |
| `meta.queryMode=raw_snapshot` | Complete snapshot uses raw Spans. |
| `meta.sessionPageSummaryStrategy=batch` | Standard bounded Session summary batch. |
| `meta.sessionTokenQueryStrategy=generated_columns` | Verified standard stored Token values. |
| `meta.readModel.reason` | Why an indexed page was unavailable; it does not imply a failed complete fallback. |

Counts, filters, facets and ordering inside a response share one storage snapshot. Separate requests can observe later completed revisions. Authentication failures, malformed successful responses and transport failures remain visible errors.

## Recovery order

1. Set the deployment key `BACKEND_QUERY_MODE=raw` and reapply the original deployment to bypass indexed reads while preserving filters. Expect a slower complete snapshot, within the stated bounds.
2. If derived schema/writes are faulty, set `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=false` and reapply all data-backend writers. The shared suspension prevents remaining locally enabled replicas from trusting old summaries. Raw mutations must continue through this controlled ingestion path.
3. Repair the cause. Before resuming, set `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=true` on all writers. Explicit resume requires both the configured SigNoz admin credential and the existing deployment-internal ingest key; an ordinary organization reader cannot reset fleet-wide state.
4. With `SIGNOZ_INGEST_INTERNAL_KEY` exported from the original private deployment configuration in the operator's shell, run:

```sh
docker exec --workdir /app --env SIGNOZ_INGEST_INTERNAL_KEY aivision-be   /app/.venv/bin/python manage.py maintain_trace_read_model --resume-maintenance --once
```

Use the actual backend container name from `aiv status` or the original `aiv status --target compose --compose-file ... --compose-env-file ...` command; Compose uses the `backend` role and may not call the container `aivision-be`. Passing the variable name inherits it from the operator environment; do not put the value in command arguments or logs. The normal backend worker does not need this secret. Resume discards rebuildable derived records, including orphan projections for deleted Traces, and increments the shared generation. Raw Spans are retained. The worker then reenrolls and refreshes data; recovery queries continue to work while it catches up.

5. Verify the same fixed queries, complete counts and Token-zero behavior. Return the same deployment key `BACKEND_QUERY_MODE=auto` after repair. There is no requirement to drop generated columns for a functional query rollback.

Manual SQL imports, retention, restore, partition/table replacement and direct raw-table writes bypass ingestion tracking. Suspend maintenance before those operations and explicitly resume/rebuild afterward. This is an operator workflow, not automatic detection of external database changes. CE and seekdb data volumes are not interchangeable; changing engines requires a separate deployment and explicit data migration.

### Kubernetes recovery process

Reapply the original fixed-CLI deployment with the same context, namespace and vars file after setting `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=true` on every writer. Obtain the existing single-line internal key through the deployment's approved Secret procedure and export it only in the authorized operator shell. Replace the context and namespace below. Send the value on stdin to a one-shot exec process; do not put it in command arguments, Pod environment, history output or logs. No bootstrap Job or serving worker is restarted by this command.

```sh
test -n "${SIGNOZ_INGEST_INTERNAL_KEY:-}" &&
printf '%s\n' "$SIGNOZ_INGEST_INTERNAL_KEY" |
  kubectl --context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
    exec -i deployment/aivision-be -c be -- /bin/sh -c \
    'IFS= read -r SIGNOZ_INGEST_INTERNAL_KEY || exit 1; export SIGNOZ_INGEST_INTERNAL_KEY; cd /app && exec /app/.venv/bin/python manage.py maintain_trace_read_model --resume-maintenance --once'
```

The retained physical Deployment/container names are `aivision-be` / `be`; role selectors do not rename them. The key exists only in this recovery process. Verify fixed queries and complete coverage, then set the same deployment key `BACKEND_QUERY_MODE=auto` and reapply through the original CLI. This procedure does not establish live-cluster validation.

## Validation boundary

See [the same-data performance and recovery report](../../testing/standard-query-defaults-20261025.md) and [the preceding opt-in comparison](../../testing/session-query-recovery-20261025.md). Local prepared-environment checks, real database SQL and external test transports are reported separately from full SigNoz authentication, released images, Kubernetes execution and production deployment.
