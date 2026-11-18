# Pi observability (observ-pi + pireplay)

Two complementary pi extensions:

| Extension | Responsibility | Scope |
|---|---|---|
| **piagent** (`pi/observ-pi`) | Real-time OTLP telemetry (traces / events / metrics) | `piagent` only — no transcript, no replay |
| **pireplay** (`pi/pireplay`) | Canonical transcript recording + replay CLI + golden superloop | `pireplay` only — no real-time OTLP |

Both subscribe to the same 16 pi lifecycle anchors. Load them together for a
full pipeline: piagent emits live OTLP to the gateway, pireplay records a
durable checksum-chained transcript that can later be replayed into OTLP files
for offline validation and golden regression.

The deterministic native-agent E2E suite pins Pi **1.0.2** and runs its real RPC
process with production `observ-pi`; the provider and OTLP receiver are local.
The production and replay development SDKs also pin **1.0.2**. Their test,
typecheck, and build commands are checked against **0.80.6** and **1.0.2**;
the Skill contracts retain **0.80.6**, **0.80.10**, and **1.0.2** npm package
integrity and source hashes. The host peer range remains unchanged.
Both development packages also pin the host's `pi-codemode` dependency to
**1.0.2**: version 1.4.0, allowed by the host's caret range, removed an export
that Pi 1.0.2 imports during startup. The isolated native installer uses the
same override without modifying the official host package.
Older **0.80.x** golden/contract fixtures remain a separate compatibility
baseline. Those versions do not expose `provider_stream_event`, so initialized
zero usage cannot be distinguished from a provider-reported zero. The plugin
keeps those measurements unknown; it does not assume they are free requests.

On Pi 1.0.2, native OpenAI-compatible usage chunks retain their reportedness:
omitted input/output stays unknown and explicit zero stays zero. Provider
`prompt_tokens` is already inclusive of cache reads/writes; the legacy Pi
normalized `input` bucket requires adding both cache buckets once. Completion
tokens already include reasoning tokens. Cache and reasoning counts are also
recorded separately when the provider reports them. Provider response content
and headers are not retained by the usage projection, including in privacy
mode. A native `parentToolCallId` links a nested tool to its observed physical
parent; a missing parent uses the Turn rather than inventing a span.

`agent.token.usage` counts fresh input (inclusive input minus reported cache
reads/writes) and inclusive output. `agent.cache.read_tokens` and
`agent.cache.write_tokens` keep the reported cache buckets separate. Unknown
measurements emit no token sample; provider-reported zero may emit zero. These
are token counters with unit `{token}` and do not duplicate Turn usage.

## Environment

### piagent (telemetry)

| Variable | Purpose |
|---|---|
| `AGENT_ENABLE_TELEMETRY=true` | Enable production telemetry (default is disabled) |
| `OBSERV_PI_DEBUG=true` | Override JSON `debug` and enable verbose diagnostics plus realtime OTLP JSONL debug artifacts |
| `OBSERV_PI_REPLAY_DIR` | Debug file output directory (realtime OTLP JSONL) |
| `OBSERV_PI_STATE_DIR` | Plugin-owned resume high-water state (default `~/.aivision/pi/state`) |
| `OBSERV_PI_SESSION_NAME` | Debug file artifact label |
| `OBSERV_PI_LOG_DIR` | Plugin diagnostic log directory (`observ-pi.log`) |
| `AIVISION_COLLECTION_MODE` / `OBSERV_PI_COLLECTION_MODE` | `full` or `privacy` |
| `AIVISION_META_TRIGGER` | Optional external task attribution (`automation`, `interactive`, or `unknown`) on the shared OTel Resource; overrides Resource configuration |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Traces endpoint |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Metrics endpoint |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | Events/logs endpoint |

piagent does NOT use `OBSERV_PI_SESSION_DIR` — transcript recording is pireplay's job.
Resume continuity uses only piagent's small per-session turn and event-sequence
high-water state; it does not read Pi history or pireplay transcripts. Event
sequence ranges are reserved in blocks, so restart continuity does not require
a synchronous state-file write for every emitted event. If a durable range
cannot be reserved, telemetry remains fail-open but omits `event.sequence`
rather than emitting a duplicate-prone process-local value.
The runtime expects one active Pi plugin process per session. A per-session
lock prevents overlapping state updates from clobbering either high-water
field; lock contention remains fail-open so telemetry delivery is not blocked.

The plugin diagnostic log records initialization, per-signal configuration,
successful lifecycle hook processing with span/log/metric counts, hook errors,
per-batch OTLP export success or failure with record counts, and shutdown flush
results. Startup entries identify the global/project config paths and whether
each file was loaded, then report the effective protocol, signal endpoints,
intervals, temporality, and header names/counts. Header values remain
debug-only and are redacted when sensitive. It never writes prompts, tool
arguments, model outputs, or session IDs. High-frequency `message_update` and
`tool_execution_update` hook entries are emitted only when `debug` is enabled
in `observ-pi.json` or by `OBSERV_PI_DEBUG=true`.

Debug mode also writes `DEBUG` entries containing each OTLP signal's endpoint
and headers. Credential-bearing URL components, sensitive query parameters,
and sensitive headers such as `Authorization`, cookies, tokens, secrets, and
API keys retain their names but have their values replaced with `[REDACTED]`.

### piagent configuration files

Piagent uses Pi's own configuration directories and the exact same production
schema as OpenClaw/OpenCode: [`docs/config/common.schema.json`](../docs/config/common.schema.json).
[`docs/config/pi.schema.json`](../docs/config/pi.schema.json) is a regular-file
copy guarded by a deep-equality regression test. The example is
[`docs/config/observ-pi.example.jsonc`](../docs/config/observ-pi.example.jsonc).

| Scope | Path | Loading rule |
|---|---|---|
| Global | `~/.pi/agent/observ-pi.json` | `PI_CODING_AGENT_DIR` is respected |
| Project | `.pi/observ-pi.json` | Loaded only when Pi marks the project trusted |

Resolution order is environment variables, trusted project configuration,
global configuration, embedded edition defaults, then runtime defaults.
Project and global files are deep-merged, so a project can override one signal
endpoint without copying the entire global `otelExporter` block.

External callers may attribute the whole Pi process/task through the optional
Resource field `otelExporter.resourceAttributes["ant.meta.trigger"]`:

```json
{
  "otelExporter": {
    "resourceAttributes": {
      "ant.meta.trigger": "automation"
    }
  }
}
```

```fish
set -x AIVISION_META_TRIGGER automation
```

`AIVISION_META_TRIGGER` has higher priority than the final merged Resource
configuration. Values are trimmed and must be `automation`, `interactive`, or
`unknown`; an explicitly empty or invalid value becomes `unknown`. When neither
entry point is configured, the Resource contains `ant.meta.trigger=""`. This is external task
attribution shared by traces, metrics, and events/logs; piagent does not infer it
from Pi history or pireplay transcripts and does not create
`agentic.turn.trigger`.

Production fields match OpenClaw/OpenCode exactly. `debug` can be set in JSON
and is overridden by `OBSERV_PI_DEBUG`; `OBSERV_PI_REPLAY_DIR` and
`OBSERV_PI_SESSION_NAME` remain environment-only artifact controls. Pireplay
still owns transcripts and offline replay.

### LLM input materialization

Pi emits provider inputs incrementally by default, with no configuration mode
or rollback switch. The first request and every reset boundary write the full
current provider message snapshot to `gen_ai.input.messages`; a strict append
writes only the new suffix; an exact retry writes the complete current provider
message snapshot again. On the first provider request after a new user Turn,
the materializer trims the previous Turn's final assistant reply and starts at
the current user message. A shorter message list,
a non-prefix rewrite, compaction, session change, model/provider scope change,
or recovery after unavailable input resets the baseline and writes the complete
current snapshot truthfully.

The comparison runs on the raw provider message sequence before privacy and
truncation projection, while only projected selected messages are persisted.
Per-message SHA-256 fingerprints stay in memory and are never written to the
transcript. `agentic.input.delta` is not emitted. Existing schema-v1 transcripts
without materialization metadata retain their historical per-request full
snapshot replay behavior.

### pireplay (transcript + replay + golden)

| Variable | Purpose |
|---|---|
| `PIREPLAY_SESSION_DIR` | Canonical transcript JSONL directory; defaults to `~/.aivision/pi/transcripts/` |
| `PIREPLAY_SESSION_NAME` | Transcript artifact label |
| `PIREPLAY_COLLECTION_MODE` | `full` or `privacy` |
| `PIREPLAY_CAPABILITY_PROBE` | Write `getAllTools()` probe on session_start (golden) |
| `PIREPLAY_GOLDEN_REUSE_RUN` | Re-evaluate an existing golden run (no model calls) |
| `PIREPLAY_GOLDEN_RUN_DIR` | Override golden run output directory |

## Commands

```bash
make -C pi build                 # build both piagent + pireplay
make -C pi test                  # test both
make install pi                  # production: register observ-pi only
make install pi-replay           # local debugging: register only pireplay
make -C pi uninstall-replay      # remove pireplay; keep observ-pi installed
make release pi                  # package common + aivision observ-pi editions
make -C pi demo                  # real pi session (loads piagent + pireplay)
make -C pi replay-all            # transcripts/*.jsonl -> replays/*.jsonl  [pireplay]
make -C pi validate              # weaver-guard validate replays/
make -C pi report                # POST replays to http://localhost:4319/
make golden-collect pi           # run 8 golden scenarios end-to-end [pireplay superloop]
make report-check-golden pi      # replay golden transcripts + POST to AiVision
```

## Editions

`make release pi` publishes only `observ-pi`, in two OpenClaw-style editions:

| Edition | Embedded defaults | Artifact |
|---|---|---|
| `common` | No managed endpoint defaults | `releases/pi/common/<version>/observ-pi-<version>.tgz` |
| `aivision` | Managed AIVision traces/metrics/events endpoints | `releases/pi/aivision/<version>/observ-aivision-pi-<version>.tgz` |

Both archives keep the npm identity `@observ/pi`; the edition is selected and
embedded at build time. Neither archive contains pireplay.

Local installation configures the three signals independently. Override them
when needed:

```bash
make install pi \
  LOCAL_OTLP_TRACES_ENDPOINT=http://localhost:4319/v1/traces \
  LOCAL_OTLP_METRICS_ENDPOINT=http://localhost:4319/v1/metrics \
  LOCAL_OTLP_LOGS_ENDPOINT=http://localhost:4319/v1/logs
```

## Architecture

Production installs only `observ-pi`. `pireplay` is an optional local debugging
extension used when transcript capture is required; offline replay commands do
not require registering pireplay with Pi.

```
pi extension hooks (15 lifecycle anchors)
├── observ-pi: normalize → SemanticEngine → OpenTelemetry SDK → 4319
└── pireplay (optional local debug): normalize → TranscriptWriter → session_*.jsonl

Offline replay (pireplay CLI):
  session_*.jsonl → SemanticEngine → file telemetry → spans_/events_/metrics_*.jsonl

Golden parity:
  piagent DEBUG files (realtime)  vs  pireplay replay files (replay)
  → compareSemanticParity checks span/event/metric projections are equal
```

observ-pi exports its `CanonicalNormalizer`, `SemanticEngine`, `createTelemetry`,
and `createPiModelConfig` so pireplay can reuse the exact same semantic mapping
without duplicating it. pireplay depends on `@observ/pi` via a `file:`
workspace link.

For compatibility with existing local tooling, `pi/piagent` is a symlink to
`pi/observ-pi`. New path references should use `pi/observ-pi`.
