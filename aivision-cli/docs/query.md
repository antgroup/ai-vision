# `aiv query` — querying AI Vision data with a SpaceKey

`aiv query` is a LangSmith-style data browser for the AI Vision
observability stack. It talks to the **AI Vision OpenAPI**
(`/openapi/v1/spaces/{space_id}/agents/{agent_product}/…`) using your
**SpaceKey** as a Bearer token.

The CLI does **no** client-side authorization: result visibility is enforced by
the backend (a business space sees the whole space, a personal space is filtered
by `work_id`, sensitive fields are gated by role). The org id and the internal
SigNoz API key are **never** involved — see [`auth.md`](auth.md).

---

## 1. Get a SpaceKey

A SpaceKey (`sk-aiv-…`) is a **space-scoped** query credential. After account
login, create or import one for the current space; the CLI saves it immediately:

```bash
aiv key create --name cli
# Or: aiv key import --key-stdin < /secure/space-key.txt
aiv connect codex              # saves the default Agent after successful installation
aiv query trace list
```

The original SpaceKey-only mode also remains available:

```bash
aiv auth login \
  --endpoint http://127.0.0.1:8001 \
  --space-key sk-aiv-... \
  --space <space-uuid> \
  --agent-product <product>     # optional; defaults to the first agent of the space
```

`auth login` verifies the key against the space by default (a wrong key or a
key/space mismatch fails immediately with a clear message). After that, every
`query` command picks the credentials up automatically.

---

## 2. Credential resolution

For each `query` command, credentials resolve by scope:

1. explicit flags: `--endpoint`, `--space-key`, `--space`, `--agent-product`
2. `--profile`, `AIVISION_PROFILE` or the active profile; keys and default Agent
   are loaded from the requested space within that profile
3. only when there is no profile: environment `AIVISION_ENDPOINT`,
   `AIVISION_SPACE_KEY`, `AIVISION_SPACE_ID`, `AIVISION_AGENT_PRODUCT`

Changing `--space` never borrows the old space's key, and changing `--endpoint`
does not send a saved key to another platform. Supply `--space-key` explicitly
when overriding credentials. Missing keys in a saved profile do not fall back
to stale exports in the parent shell.

Apply the active profile to your current shell at any time:

```bash
eval "$(aiv auth print-env)"
```

If any of endpoint / space key / space id / agent product is still missing, the
command stops with a message telling you exactly which one and how to supply it.

---

## 3. Output formats

| Flag | Effect |
|---|---|
| `--format table` (default except `trace metrics`) | human-readable table; falls back to raw JSON when the response is not a row set |
| `--format json` | the `data` payload as JSON |
| `-o FILE` / `--out FILE` | write to `FILE` instead of stdout |
| `export <dir>` sub-commands | write one JSONL file per record into `dir` |

---

## 4. Time ranges

`--since` / `--until` accept: RFC3339 (`2026-09-13T10:00:00Z`), epoch
milliseconds or seconds, the literal `now`, or a **relative duration**
(`90s`, `15m`, `6h`, `2d`, `1w`; a trailing `ago` is allowed). Defaults are
`since = 1h ago`, `until = now`. `--last-n-minutes N` is shorthand for
`--since Nm --until now`. Times are sent to the API as epoch milliseconds.

---

## 5. Commands

### Traces

```bash
aiv query trace list --since 6h --limit 20 --order-by duration_ms --format table
aiv query trace list --filter '[{"field":"status_code","op":"=","value":2}]' --fields trace_id,duration_ms,status_code
aiv query trace get <trace-id> --format json
aiv query trace export ./out --limit 50 --filename-pattern '{trace_id}.json'
```

- `trace list` → `POST .../traces/list`
- `trace get <id>` → `GET .../trace/{id}/detail`
- `trace export <dir>` → lists traces then writes each trace's detail as a JSONL
  line. `--filename-pattern` supports `{trace_id}`; paths are contained inside
  `<dir>` (a trace id can never escape the output directory).

### Trace metrics (full time window)

```bash
aiv query trace metrics --since 24h --bucket hour --group-by model
aiv query trace metrics --profile work --agent-uuid <agent-instance-uuid> \
  --since 2025-01-01T00:00:00Z --until 2025-01-02T00:00:00Z \
  --filter '[{"field":"status_code","op":"IN","value":[1,2]}]' -o metrics.json
```

`trace metrics` sends `POST .../traces/metrics` using the selected profile's
endpoint, space, Agent product and SpaceKey. `--agent-uuid` optionally narrows
that scope to one Agent instance. `--filter` accepts the native trace query's
JSON array of `{field, op, value}` conditions; `IN` uses a value array.
`--bucket minute|hour|day` defaults to `hour`; `--group-by model|provider` is
optional and accepts one dimension. Output is the complete JSON aggregate;
`--format json` is the default and only supported format for this command.

The server aggregates the entire selected time window, with matching filters.
There are no `--limit` or `--page` flags. A scan exceeding **10,000 traces**
fails explicitly; narrow the window instead of treating a first page as totals.
HTTP failures and a response without `meta.complete: true` fail the command
without writing an output file. Do not replace a failure with `[]` or zero.

The result contains `traceCount`, `spanCount`, `generationCount`, `toolCount`,
`errorCount`, `errorRate`, canonical `usage` (tokens and cost), `coverage`,
`latencyMs` (`mean`, `p95`), `timeseries`, `groups`, and
`meta` (`complete`, `from`, `to`, `filters`). Missing measurements remain null.
In particular, a missing generation's usage can leave `usage.total` null while
`coverage.knownTokens` reports a known subtotal; compare
`coverage.knownUsageCalls` with `coverage.billableCalls` before describing a
total. `meta.complete` describes retrieval completeness, not measurement
coverage. Inspect the returned cost provenance and currency rather than
substituting model prices or zero.

### Spans

```bash
aiv query trace get --format json <trace-id>   # all spans of a trace
aiv query span list <trace-id> <span-id> ...   # one or more known span IDs
aiv query span get <trace-id> <span-id>
```

- `span list <traceId> <spanId>...` → `POST .../trace/{id}/spans`; at least one
  span ID is required. Get the IDs from the trace detail, and put flags before
  all positional IDs for batch requests.
- `span get <traceId> <spanId>` → `GET .../trace/{id}/span/{span_id}`

For native detail and span reads, pass `--param from=<epoch-ms>` and
`--param to=<epoch-ms>` with the same frozen bounds as the trace list, plus
`--param agent_uuid=<Agent_UUID>` when selecting one instance. These commands
do not forward `--since` / `--until`.

For `span get`, flags can appear before, after or between the two IDs. Unknown
flags, missing flag values and extra IDs fail before sending a request.

### Sessions

```bash
aiv query session list --limit 20
aiv query session get <session-id>
aiv query session io <session-id>              # inputs/outputs
aiv query session usage <session-id> ...        # token usage (one or more)
```

For `session usage`, flags can appear before, after or between session IDs:

```bash
aiv query session usage session-one session-two \
  --since 2025-01-01T00:00:00Z --until 2025-01-02T00:00:00Z --format json
```

Unknown flags and missing flag values fail before a request is sent. Use `--`
before positional IDs that start with a dash.

- `session list` → `POST .../sessions/list`
- `session get <id>` → `GET .../session/{id}/detail`
- `session io <id>` → `POST .../session/{id}/io`
- `session usage <id>...` → `POST .../sessions/usage`

### Logs / events

```bash
aiv query log list --since 30m --limit 50
aiv query log list --filter "severity_text = 'ERROR'"
aiv query log query --mode fieldsJson --filter "..." --fields timestamp,body
aiv query log query --mode rawSql --sql "SELECT ... FROM ..."
aiv query log export ./out --limit 200         # events.jsonl
```

- `log list` → `POST .../events/list`
- `log query` → `POST .../events/query` (`--mode fieldsJson|rawSql`; filters are
  passed through only in `fieldsJson` mode)
- `log export <dir>` → writes `events.jsonl`

> `rawSql` is only accepted by the backend for SpaceKey-authenticated callers and
> is validated server-side; the SQL you pass is your own query, not injected.
> Before it runs, the CLI reads metadata capabilities and returns a local error
> when `events.modes.rawSql` is unavailable. The rawSql request only sends the
> backend's supported time, format, reference-id and SQL fields.

### Raw SQL metrics (timeseries)

```bash
aiv query metric query --sql "SELECT ... " --ref-id A --format json
```

- `metric query` → `POST /openapi/v1/grafana/spaces/{space}/agents/{product}/query`
  (`--sql` is required; `mode` is `rawSql`).

### Metadata

```bash
aiv query metadata            # space/agent metadata
aiv query metadata events     # available event fields
aiv query metadata traces     # available trace fields
aiv query metadata events --param agent_uuid=<Agent_UUID>  # select one instance
```

- `metadata` → `GET .../metadata`
- `metadata events` → `GET .../metadata/events`
- `metadata traces` → `GET .../metadata/traces`

When a product has multiple Normal or Pending Agent instances in the selected
Space, use `--param agent_uuid=<Agent_UUID>`. The same option selects the Agent
for `query log list`, `query log query --mode fieldsJson`, and `query log export`,
including their metadata capability checks. The server rejects ambiguous,
Disabled, foreign-space, or foreign-product selections. This does not extend
the allowed raw SQL fields.

---

## 6. Common flags

`--endpoint`, `--space-key`, `--space`, `--agent-product`, `--format table|json`,
`-o/--out FILE`, `--limit/-n`, `--page`, `--since`, `--until`,
`--last-n-minutes`, `--order-by`, `--fields a,b`, `--filter EXPR`, and
`--param k=v` (repeatable pass-through for any body/query parameter the dedicated
flags do not cover; the value is parsed as JSON when possible).

---

## 7. Building a dataset (JSONL export)

```bash
aiv query trace export ./dataset --since 24h --limit 500
# -> ./dataset/traces.jsonl   (one trace detail per line)

aiv query log export ./dataset --since 24h --limit 5000
# -> ./dataset/events.jsonl
```

JSONL is convenient for `jq`, pandas or loading into another tool:

```bash
jq -c 'select(.duration > 1000)' ./dataset/traces.jsonl
```

---

## 8. Errors

| Symptom | Meaning |
|---|---|
| `401` / code `4001` | missing or rejected SpaceKey — re-run `aiv auth login` |
| `403` / code `4003` | the SpaceKey does not match the requested space |
| `404` / code `3001` | the space does not exist |
| `no endpoint / space key / space id / agent product` | that credential was not supplied — see §2 |

The response envelope is `{"code":0,"message":"success","data":...}`; on error the
CLI prints the `code` and `message` from the server so the cause is visible.
