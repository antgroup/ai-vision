# observ-hermes

For Hermes `v0.22.5`, the AI Vision marketplace offers only the official
Hermes-owned NeMo Relay integration, with no AI Vision source checkout required. It uses one
OpenInference trace exporter and the existing Hermes identity. See
[the integration and validation record](docs/RELAY_INTEGRATION.md) for setup,
actual end-to-end evidence, and upstream coverage limits.
The native guide includes common-credential redaction, optional email/phone
matching, and explicit field omission. See [the privacy contract and opt-in
acceptance commands](docs/RELAY_PRIVACY.md) for coverage and limitations.
Follow the [official Relay onboarding guide](https://aivision.mintlify.site/en/integrations/hermes)
for connections. The collector implementation and development workflows below
are engineering references and are not marketplace onboarding options.

`observ-hermes` is the Hermes Agent observability plugin for AiVision. Version
`0.2.2` supports the baseline PyPI runtime `hermes-agent==0.18.2` and the audited
Hermes `0.21.5` source revision with observer schema `hermes.observer.v1`; it emits
OpenTelemetry traces, logs, and metrics that are validated against the repository's normative `docs/semconv/` definitions and
`weaver-guard` v4 policy set.

This plugin is intentionally narrow:

- Production `observ-hermes` emits live telemetry and never writes replay
  transcripts or OTLP replay files.
- Developer-only `hermes-replay` is a separate package/plugin. When explicitly
  enabled it records versioned raw callback envelopes before normalization.
- Live Hermes hooks and offline replay share the neutral
  `aivision-hermes-core==0.2.2` normalizer and `SemanticEngine`.
- Hook failures remain isolated from Hermes and are counted by hook/error type
  without retaining payload or exception content.
- Replay validates its raw-envelope checksum chain before offline normalization
  and transactional OTLP JSONL publication.
- No credentials are bundled in tracked config. Runtime OTLP headers are read
  from environment variables.

## Compatibility

| Component | Required version or source |
| --- | --- |
| Hermes Agent | Baseline PyPI `hermes-agent==0.18.2`; current source `v2026.9.24` / `0.21.5` at `f97608f178d1ffeca59860195ab7da295f7c8e5f` |
| Hermes observer schema | `hermes.observer.v1` |
| Plugin | `observ-hermes 0.2.2` |
| Python | Plugin requires Python >=3.10; native acceptance uses Python 3.12 |
| Semconv | Repository `docs/semconv/` |
| Validator | Repository `weaver/weaver-guard` v4.x policy inventory |

The executable hook contract is documented in
[`docs/HERMES_V0_18_2_HOOK_CONTRACT.md`](docs/HERMES_V0_18_2_HOOK_CONTRACT.md).
The current-runtime differences and source limits are documented in
[`docs/HERMES_V0_21_5_COMPATIBILITY.md`](docs/HERMES_V0_21_5_COMPATIBILITY.md).
The resumed-process and real automatic-compression results are recorded in
[`docs/HERMES_TURN_NUMBERING_ACCEPTANCE.md`](docs/HERMES_TURN_NUMBERING_ACCEPTANCE.md).
Regenerate the baseline fixture only against Hermes `0.18.2`:

```bash
python3 scripts/capture_hermes_v0182_hooks.py
```

PyPI uses the semantic version `0.18.2`; `2026.7.7.2` is the package's
`__release_date__` and an upstream date tag, not a PyPI version to install.
The pinned runtime exposes the 13 hooks in the fixture plus the official
`llm_execution` middleware. Main model calls use that middleware to retain
only reported integer usage counters and native session/request IDs in memory.
The provider response is returned unchanged; errors and cancellation propagate
to Hermes. Counter facts are consumed once and cleared on session termination.
Missing input/output buckets retain the semconv-required numeric placeholder
with `agentic.usage.*.missing=true`; a provider-reported zero has no missing flag.
An unknown bucket never produces a fabricated total or token metric.
Spans and API events preserve reported cache read, cache creation and reasoning
subsets as `gen_ai.usage.cache_read.input_tokens`,
`gen_ai.usage.cache_creation.input_tokens` and
`gen_ai.usage.reasoning.output_tokens`. OpenAI prompt totals already include
cache subsets; the input metric subtracts those reported subsets. Anthropic
fresh input is combined with reported cache buckets for the span's input total.
Reasoning tokens remain a subset of output and are never added to it again.
An inconsistent cache subset does not produce a negative fresh input metric.
Hermes 0.18.2 starts text continuation after a chat-completions response with
`finish_reason=length` before emitting its normal completion hook. The plugin
settles that physical response at the official middleware return boundary,
using its actual assistant content, reported counters and observed completion
time. A later host completion hook cannot count it twice. Interrupted-stream
stubs and content-filter failures remain on the error/cancellation path;
responses containing tool calls retain the ordinary host completion boundary.

Newer upstream versions additionally expose `pre_auxiliary_call` and
`post_auxiliary_call`. The plugin registers them only when the host declares
them. Turn-associated auxiliary attempts have separate span state for their
native request ID, task and retry count. The newer hook currently exposes
normalized usage without raw field presence: positive counters are retained,
while zero buckets and streamed auxiliary usage remain unknown. Turnless
auxiliary work has no supported conversation association. This compatibility
adapter has source-contract tests and native 0.21.5 acceptance for scoped title
and automatic-compression requests. The retained 0.18.2 fixture has no auxiliary
hooks; neither result establishes completeness for turnless auxiliary work.

Native NVIDIA configuration is supplied by Hermes itself:

```yaml
model:
  provider: nvidia
  default: nvidia/llama-3.1-nemotron-70b-instruct
plugins:
  enabled: [observ-hermes]
```

Set `NVIDIA_API_KEY` in the Hermes process environment. The pinned
[NVIDIA provider profile](https://github.com/NousResearch/hermes-agent/blob/v2026.7.7.2/plugins/model-providers/nvidia/__init__.py)
uses NVIDIA's hosted endpoint. The
[newer provider implementation](https://github.com/NousResearch/hermes-agent/blob/main/plugins/model-providers/nvidia/__init__.py)
adds tool-message field cleanup; the pinned plain profile does not provide that
behavior. Provider reachability and model-specific tool compatibility require
their own live check. The native fake-provider suite exercises Hermes' real
CLI, plugin loader, tools, resume, stream and cancellation; LangChain supplies
only the local fake HTTP model. Hosted validation must count every physical
request, including retries, model discovery and auxiliary requests, against
the configured RPM budget. A single session or span count cannot establish it.

Native cancellation acceptance uses the official `hermes acp` stdio protocol;
install `hermes-agent[acp]==0.18.2` for that scenario. The runner obtains the
native `session/new` identity, waits for an actual text delta, sends
`session/cancel`, and requires `stopReason=cancelled` plus successful shutdown
and terminal telemetry. It does not emulate Windows console interrupts.
The ACP path has macOS native evidence; Windows support remains subject to the
Windows matrix result and is not established by that evidence.

Production collection continues turn numbering across CLI processes using a
collector-owned SQLite ledger at
`$HERMES_HOME/observ-hermes-state/turn-numbers.sqlite3` (default
`~/.hermes/observ-hermes-state/turn-numbers.sqlite3`). It is created only after
telemetry is enabled and a native turn is observed. The ledger contains hashes
of collection scope, native session/turn IDs, and assigned ordinals; no messages,
transcripts, credentials, or host database fields are stored. SQLite commits the
allocation before emission and serializes concurrent processes. A repeated
native turn ID reuses its number; internal API retries and length continuations
do not allocate additional turns. Original IDs, timestamps, parents and usage
are unchanged.

The number means **the collector's observed order within one collection scope**,
starting at 1 when that scope first observes the conversation. It does not count
turns before installation or while collection was disabled. Export destinations,
routing headers, or configured identity changes start an independent sequence.
Keep this state directory to preserve numbering; deleting it starts a new
observation sequence. Identity remains `gen_ai.chat.id`; order by original start
time when comparing independent collectors or older telemetry.

State allocation errors are handled by the existing fail-open plugin hook
boundary: Hermes continues, a content-free `hook failed` warning/counter records
the failure, and the affected hook emits no invented ordinal. A corrupt ledger
is never overwritten or silently reset. Restore it from a backup to continue
its sequence. New state directories/files are private; existing parent directory
permissions are not changed.

Offline replay remains independent and never reads or writes the production
ledger. Its turn numbers are local to the supplied capture, starting at 1;
partial captures cannot recover a missing earlier observation prefix. Both
paths still share the same semantic reducer and original native identities.

## Collector development setup

The AI Vision marketplace provides only official Relay onboarding. The source
commands below set up the collector implementation for development and do not
appear in the marketplace. The catalog uses `setup=OTHER` and `source_install=true`
to exclude a generic `aiv connect` package installer. Collector implementation
does not establish AgentLens diagnosis support.

First create a Hermes Agent in the target Space and ensure its active
AgentTypeConfig has trace, metric and log DataSources pointing to that Space's
deployed query backend. Keep the assigned `agent_uuid`. Obtain an ingest token
from the platform using the SpaceKey:

```http
POST /openapi/v1/ingest-tokens
Authorization: Bearer <SpaceKey>
Content-Type: application/json

{"agent_uuid":"<registered-agent-uuid>","ttl_seconds":900}
```

The supported route is `SpaceKeyMintTokenView`, registered by
`aivision_backend/urls.py` and `apps/core/urls/openapi/observability.py`.
`data` in a successful response contains `token`, `token_type`, `expires_in`,
`expires_at`, `endpoint`, `space_id`, `agent_uuid` and `agent_product`.
The requested TTL must not exceed the deployment limit (900 seconds by default).
Send the returned short-lived ingest token to the returned collector endpoint.
The SpaceKey is used only to mint/renew the token on the platform.

Create `$HERMES_HOME/observ-hermes.json` with the actual response endpoint and
the registered product identity:

```json
{
  "enableTelemetry": true,
  "collectionMode": "full",
  "otelExporter": {
    "protocol": "http/json",
    "resourceAttributes": {
      "ant.agent.type": "hermes",
      "ant.agent.product": "Hermes",
      "ant.agent.instance": "<registered-agent-uuid>"
    }
  },
  "tracesConfig": {"enabled": true, "endpoint": "<endpoint>/v1/traces"},
  "metricsConfig": {"enabled": true, "endpoint": "<endpoint>/v1/metrics"},
  "eventsConfig": {"enabled": true, "endpoint": "<endpoint>/v1/logs"}
}
```

Supply `AIVISION_ANT_USERNAME`, `AIVISION_ANT_UID` and
`OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <ingest-token>` in the
process environment. Match `ant.agent.product` to the token response and
assigned AgentTypeConfig. Keep bearer tokens out of tracked configuration.
Before `expires_at`, mint a new token and restart the exporting Hermes process
with the new environment; the current exporter does not automatically rotate
its headers. A 401/403 from the gateway is an ingestion failure, not successful
observation. Check token expiry/revocation and the registered scope before retry.

Validate in stages: source installation and hook registration; native file-read
receipt plus provider/tool/call correlation; admitted trace/metric/log ingress;
then API/UI readback in the assigned Space and dashboard usage coverage. Missing
usage must remain unknown through readback. The local fake-provider pass alone
does not establish hosted NVIDIA or platform verification.

## Install

Install from the `hermes/` directory:

```bash
make install
```

Or use the unified repository Makefile from the repository root:

```bash
make install hms
```

The install target deploys only the production `observ-hermes` plugin. It does
not install `hermes-replay`, `observ-hermes-replay`, or
`observ-hermes-report`.

Install the developer replay product explicitly:

```bash
make replay-plugin-install
# repository root equivalent
make install hms-replay
```

That target installs `hermes-replay` plus its exact
`aivision-hermes-core==0.2.2` dependency and exposes exactly these replay-owned
commands:

```text
hermes-replay
observ-hermes-replay
observ-hermes-report
```

Capture remains disabled until `$HERMES_HOME/hermes-replay.json` contains
`"captureEnabled": true` or
`HERMES_REPLAY_CAPTURE_ENABLED=true` is set. Replay installation never enables
or reconfigures production telemetry.

When production and replay must change core version together, use the cohort
target instead of a one-sided upgrade:

```bash
make install-cohort
make rollback-cohort
# repository root install alias
make install hms-cohort
```

Uninstalling either product preserves its sibling. The shared core is removed
only after the last declaring product is removed; replay transcripts are never
removed by uninstall.

The installer compiles and validates the staged plugin, then atomically swaps it
into:

```text
~/.hermes/plugins/observ_hermes
```

Rollback state is deliberately stored outside Hermes' recursive plugin scan:

```text
~/.hermes/plugin-backups/observ_hermes.previous
```

This avoids the legacy early-plugin problem where
`~/.hermes/plugins/observ_hermes.previous` could be scanned as a second plugin
and selected instead of the current copy.

Use a different edition or destination when needed:

```bash
make install EDITION=common
make install INSTALL_DIR="$HOME/.hermes/plugins/observ_hermes"
```

Rollback restores the retained previous plugin:

```bash
make rollback
```

The install target creates or validates the strict production JSON config with
mode `600`:

```text
~/.hermes/observ-hermes.json
```

Edition defaults, the JSON file, process environment, generic OTEL environment,
and `OBSERV_HERMES_*` environment variables are resolved in documented priority
order. Legacy `observ-hermes.env` is migrated atomically through an allowlist.

## Runtime Configuration

Production reads exactly one user file:

```text
$HERMES_HOME/observ-hermes.json
```

`HERMES_HOME` defaults to `~/.hermes`. Start from the strict checked-in example
at [`../docs/config/hermes.example.jsonc`](../docs/config/hermes.example.jsonc).
The runtime validates the same public shape as
[`../docs/config/common.schema.json`](../docs/config/common.schema.json) without
requiring a JSON-Schema library at runtime.

Resolution is deterministic, from lowest to highest priority:

1. built-in defaults;
2. selected edition defaults (`common` or `aivision`);
3. `$HERMES_HOME/observ-hermes.json`;
4. generic `OTEL_*` / `AIVISION_*` environment variables;
5. plugin-specific `OBSERV_HERMES_*` environment variables.

Maps merge at each level. Signal-specific headers override colliding global
headers. Unknown fields and invalid values fail closed. In particular,
production rejects `ant`, public `log`, `sessionDir`, `replayDir`, and
`captureEnabled`.

Common environment overrides include:

| Variable | Meaning | Default |
| --- | --- | --- |
| `AGENT_ENABLE_TELEMETRY` | Enables plugin telemetry registration | edition value, `false` in `common` |
| `OBSERV_HERMES_EDITION` | Loads `observ_hermes/configs/{edition}.json` | unset |
| `AIVISION_ANT_USERNAME` | User identity attribute | unset |
| `OBSERV_HERMES_USER_ID` | Stable user id attribute | unset |
| `OTEL_SERVICE_NAME` | OTel service name | `hermes` |
| `OBSERV_HERMES_COLLECTION_MODE` | `full` or `privacy` | `full` |
| `AIVISION_COLLECTION_MODE` | Fallback collection mode | `full` |
| `OBSERV_HERMES_DEBUG` | Enables verbose production diagnostics; never replay capture or OTLP/replay file output | `false` |
| `OBSERV_HERMES_LOG_DIR` | Plugin-owned diagnostic log directory; overrides `AIVISION_LOG_DIR` and file/edition defaults | edition default |

Production diagnostics use the private, bounded, rotating
`observ-hermes.log`. `debug` adds verbose diagnostic records; normal lifecycle
records remain available when the diagnostic directory is configured. The
common edition defaults to `$HERMES_HOME/logs`; the AIVision edition defaults
to `$AIVISION_HOME/hermes/logs`. This sink never records callback payloads or
credentials, never enables replay capture, and never creates transcript,
replay, or OTLP JSONL artifacts.

Replay uses a separate strict file:

```text
$HERMES_HOME/hermes-replay.json
```

Its only public fields are `captureEnabled`, `collectionMode`, `sessionDir`,
`sessionName`, and `debug`; see
[`../docs/config/hermes-replay.example.jsonc`](../docs/config/hermes-replay.example.jsonc).
Capture is disabled by default. Production config, telemetry enablement, OTLP
headers, and production debug can never enable it.

Canonical artifact paths:

| Artifact | Environment override | Default path |
| --- | --- | --- |
| Replay transcript | `HERMES_REPLAY_SESSION_DIR` | `$AIVISION_HOME/hermes/transcripts`, only after explicit replay enablement |
| Replay output | `REPLAY_DIR` in Make targets | `$AIVISION_HOME/hermes/replays` |
| Golden | `GOLDEN_DIR` in Make targets | `$AIVISION_HOME/hermes/golden` |
| Golden baselines | `GOLDEN_BASELINE_DIR` in Make targets | `$AIVISION_HOME/hermes/golden/baselines` |

`AIVISION_HOME` defaults to:

```text
~/.aivision
```

Empty path override values are treated as unset. Runtime artifact directories
are not versioned in Git and are shared across worktrees.

## OTLP Export

Base OTLP settings:

| Variable | Meaning |
| --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base OTLP endpoint |
| `OTEL_EXPORTER_OTLP_HEADERS` | Comma-separated `key=value` runtime headers |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Protocol, default `http/protobuf` |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Default `delta` |

For `http/json` delta metrics, each series starts at the exporter's actual
creation time. Later points start at that series' previous
export timestamp; counters retain each interval's increment. Series are separate
by metric name, kind, unit, and attributes. A resumed process creates a new
stream boundary. Replay uses its earliest source observation, without inventing a duration or
substituting the current wall clock.

Per-signal overrides:

| Signal | Endpoint | Headers |
| --- | --- | --- |
| Traces | `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `OTEL_EXPORTER_OTLP_TRACES_HEADERS` |
| Metrics | `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `OTEL_EXPORTER_OTLP_METRICS_HEADERS` |
| Events/logs | `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | `OTEL_EXPORTER_OTLP_LOGS_HEADERS` |

Tracked edition configs must not contain authorization headers, API keys, or
tokens. Inject credentials only through runtime environment variables such as:

```bash
export OTEL_EXPORTER_OTLP_TRACES_HEADERS='Authorization=Bearer ...'
```

Header dictionaries are excluded from config `repr()`, and tests reject
credential-bearing edition configs. The repository still requires external
credential rotation before release approval; see
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## Privacy

`full` mode preserves available prompt/tool/model fields after recursive
redaction and bounded projection. `privacy` mode hides content-bearing fields
while preserving semantic correlation, model, status, usage, duration, and low
cardinality classification.

Secret-shaped keys and values such as `Authorization`, `token`, `secret`,
`password`, and private-key material are redacted before replay persistence or
telemetry export.

## Architecture

Live path:

```text
Hermes v0.18.2 hook
  -> hook adapter
  -> CanonicalNormalizer
  -> shared SemanticEngine
  -> production OTel HTTP exporters
```

Replay path:

```text
explicit hermes-replay raw callback JSONL
  -> raw schema + checksum-chain validation
  -> offline CanonicalNormalizer
  -> shared SemanticEngine
  -> transactional spans/events/metrics JSONL files
  -> weaver-guard validate
```

Production and replay share semantic construction, not configuration,
enablement, persistence, exporter state, credentials, or lifecycle.
The shared semantic observation stream is identity-neutral: production
username and uid are injected only by the production emission projection.
Replay therefore never reads production identity configuration. Privacy replay
capture projects content once at the persistence boundary; offline replay
sanitizes those stored markers without hashing them a second time.

The semantic topology is:

```text
Turn span
├── LLM/API request span
├── Tool span
├── Tool span
└── Agent/subagent span
```

LLM/API, tool, and subagent spans are direct children of the turn. Tool spans
are classified as `builtin`, `mcp`, or `skill`; subagents use AGENT semantics
instead of being reported as tool calls.

## Supported Hooks

The v0.18.2 observer contract covers:

- `on_session_start`
- `on_session_end`
- `on_session_finalize`
- `on_session_reset`
- `pre_llm_call`
- `post_llm_call`
- `pre_api_request`
- `post_api_request`
- `api_request_error`
- `pre_tool_call`
- `post_tool_call`
- `subagent_start`
- `subagent_stop`

Important boundaries:

- API hooks, not coarse LLM hooks, are the source for LLM spans, token usage,
  request events, and API RED metrics.
- Native `tool_call_id` is primary; synthetic IDs are compatibility fallback
  only.
- MCP and skill coverage depends on Hermes exposing those calls as physical
  tool hooks.
- Hermes v0.18.2 exposes no compaction hook. Compaction telemetry is not
  inferred from history shrinkage.

## Operations

Build and unit-test the plugin:

```bash
make build
make test
make test-production
make test-replay
```

List current artifact directories:

```bash
make list-artifacts
```

Replay all raw callback transcripts:

```bash
make replay-all
```

Replay/report targets require the explicitly installed `hermes-replay`
package. If it is absent they fail before creating the replay output directory
and print the bounded install hint.

Replay one transcript:

```bash
make replay TS=session_20260720_180439_d40d6d_464e0983698b0378.jsonl
make replay TRANSCRIPT=/path/to/session.jsonl REPLAY_DIR=/tmp/hermes-replays
```

Validate all replay files:

```bash
make validate
```

Validate one replay file:

```bash
make validate-one FILE="$HOME/.aivision/hermes/replays/spans_session_x.jsonl"
```

Upload/report current replay files to a local AiVision endpoint:

```bash
make report AIVISION_URL=http://127.0.0.1:4319
make report-shift AIVISION_URL=http://127.0.0.1:4319
```

Clean derived replay files only:

```bash
make clean
```

## Golden Workflow

Golden data lives under:

```text
$AIVISION_HOME/hermes/golden
```

Collect real Hermes scenarios:

```bash
make golden-collect AIVISION_URL=http://127.0.0.1:4319
```

Finalize provenance after validation:

```bash
make golden-finalize AIVISION_URL=http://127.0.0.1:4319
```

Initialize baselines from validated replay files:

```bash
make golden-init
```

Check current replay output against baselines:

```bash
make golden-check FORMAT=json
```

Post-split release evidence must be regenerated from explicit full/privacy raw
replay captures. Pre-split canonical transcript baselines are not accepted as
evidence for this package boundary.

Fresh post-split verification for this release candidate:

| Path | Signals | Violations | Semconv issues | Improvements | Golden |
| --- | ---: | ---: | ---: | ---: | ---: |
| Live full | 14 | 0 | 0 | 2 | — |
| Live privacy | 14 | 0 | 0 | 2 | — |
| Replay full | 13 | 0 | 0 | 10 | 3/3 |
| Replay privacy | 13 | 0 | 0 | 10 | 3/3 |

The privacy artifact scan contained neither the configured content canary nor
the credential canary. Full mode retained the content canary as designed while
still removing the credential canary. The pinned
`hermes-agent==0.18.2` fixture contract also passed in an isolated environment.

Honest skips in that evidence:

- `mcp-tool`: skipped when no configured MCP server was available.
- `skill-call`: skipped when no installed Hermes skill was available.
- `api-error`: skipped because a safe isolated provider/API credential failure
  was not authorized.
- `compaction`: skipped because Hermes v0.18.2 has no documented compaction
  observer hook and heuristic inference is forbidden.

## Release Gate

Before releasing `observ-hermes 0.2.2`:

1. `make test` must pass.
2. Full and privacy `make replay-all` plus `make validate FORMAT=json` must show
   `0` violations and `0` unknown semconv attributes.
3. Golden provenance must show `complete: true`.
4. Tracked config must contain no bundled credentials.
5. External credential rotation must be verified by the credential owner for
   `EXTERNAL-ROTATION-REQUIRED-HERMES-2026-07-20`.

Repository cleanup is not proof that a previously exposed credential was
rotated.

## Related Files

| Path | Purpose |
| --- | --- |
| `observ_hermes/pipeline.py` | Production live normalization and semantic pipeline |
| `observ_hermes/semantic.py` | Shared live/replay semantic reducer |
| `hermes-core/` | Neutral exact-version semantic core |
| `hermes-replay/hermes_replay/` | Replay-only capture, offline replay, and loopback report client |
| `observ_hermes/config.py` | Strict production JSON, edition, env, and OTLP configuration |
| `scripts/install_plugin.py` | Atomic install and rollback implementation |
| `scripts/hermes_cohort.py` | Exact-core cohort compatibility and atomic staging |
| `scripts/golden_collect.py` | Real-session golden scenario collector |
| `scripts/golden_finalize.py` | Golden provenance finalizer |
| `docs/HERMES_V0_18_2_HOOK_CONTRACT.md` | Pinned Hermes observer contract |
| `../docs/semconv/` | Normative semantic convention registry |
| `../weaver/weaver-guard/` | Validation engine |
