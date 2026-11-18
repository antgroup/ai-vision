# observ-opencode

OpenCode Agent Observability Plugin — OpenTelemetry instrumentation for OpenCode CLI.

## Overview

This plugin captures agent lifecycle events (tool execution, message updates, session lifecycle) and exports them via OpenTelemetry OTLP protocol to Langfuse or other OTLP-compatible backends.

`observ-opencode` is the production HTTP-only profile. For local transcript
capture, DEBUG file dual-write, offline replay, report, or report-shift, use the
sibling `opencode-replay` package instead of loading both plugins together.

Repository-local installation keeps that choice explicit: `make install ocd`
registers this production profile, while `make install ocd-replay` replaces it
with the local replay profile.

## Features

- **Tool-level Tracking**: Real-time telemetry for each tool execution
- **Token Usage Metrics**: Capture input/output token counts and model information
- **Session Lifecycle**: Full trace from session creation to idle state
- **Ant Spec v2.0**: Follows AI Vision Agent Observability Specification
- **Configurable Logging**: Fine-grained control over what content is logged

## Provider usage and native validation

For explicitly configured providers using `npm: "@ai-sdk/openai-compatible"`,
the plugin uses OpenCode's public `config` and `chat.headers` hooks to observe
actual Chat Completions response usage before the host normalizes missing fields
to zero. It preserves the original fetch, AbortSignal, response bytes and
backpressure. Headers, API keys and response text are not retained. Parsing is
bounded to 64 × 1024 characters per SSE line / JSON response; values outside that bound stay
unknown.

Missing input/output values use required numeric placeholders with the registered
`agentic.usage.*.missing` markers. Unknown optional totals are omitted; an explicit
reported zero remains zero. Generation spans and request events use inclusive
input/output and the standard reasoning/cache fields. Input token counters count
fresh input, with cache counters reported separately. Turn spans do not repeat
aggregate token usage.

Primary calls keep their native assistant-message identity and emit one request
event/count per observed physical ChatCompletions attempt, including a failed
429 request and its recovery using the same native assistant message. Native
abort and wire termination can arrive in either order: the fetch retains its
owning generation until usage and RED metrics settle, without duplicate events.
Actual title/summary/compaction requests
without an assistant message create separate LLM roots linked to the real source
session and agent; their content is redacted rather than guessed from the active
turn. Unsupported protocols, other retry paths and full supplier billing
reconciliation still need separate coverage.

The repository's native acceptance driver runs official OpenCode 1.18.34 with
isolated home/config/state, an actual tool reading a random workspace receipt,
and loopback provider/OTLP services. It uses the normal production plugin loader
and the official session abort API for cancellation. Those local fake-provider
checks prove the application/plugin path; real provider success, platform
readback, metrics and UI require their own evidence. The scripts
`test-llm-usage-terminal.ts` and `test-provider-wire-usage.ts` cover hook lifecycle
and transport regressions, including unavailable vs zero usage and late final
messages after cancellation.

## Installation

### As an npm package

```bash
npm install observ-opencode
```

### Configuration

Add the plugin to your `opencode.json`:

```json
{
  "plugin": [["observ-opencode", {
    "enableTelemetry": true,
    "otelExporter": {
      "endpoint": "http://localhost:4318"
    }
  }]]
}
```

The published ESM bundle supplies its own Node `createRequire` compatibility
binding, so OpenCode Desktop can load `"observ-opencode"` directly. No external
loader or `globalThis.require` injection is required. Repeated workspace loads
inside one Desktop process reuse the same initialized hooks and OTel runtime.

OpenCode invokes every distinct function exported by the plugin module as a
plugin factory. The package root therefore exports only the default and named
`ObservOpencodePlugin` factory, plus TypeScript types. Programmatic helpers use
separate entry points:

```ts
import { parseConfig, resolveStandaloneConfigPath } from 'observ-opencode/config';
import { initObservabilityTelemetry } from 'observ-opencode/http-runtime';
```

Consumers that previously imported these helper functions from the package root
must update their import path. The existing `observ-opencode/core` and
`observ-opencode/http-runtime` APIs remain available for replay and other
programmatic adapters. Register only the package root as an OpenCode plugin.

The standalone plugin config follows the active OpenCode config location:

- When `OPENCODE_CONFIG=/path/to/profile/opencode.json`, the plugin reads
  `/path/to/profile/observ-opencode.json`.
- Otherwise, when `OPENCODE_CONFIG_DIR=/path/to/profile`, the plugin reads
  `/path/to/profile/observ-opencode.json`.
- Without either variable, it reads `~/.config/opencode/observ-opencode.json`
  (or the embedded edition's configured subdirectory).

This allows multiple OpenCode processes to use isolated plugin configurations.

### Production VCS Resource attributes

When telemetry is enabled and OpenCode classifies the project as Git, the
production `ObservOpencodePlugin` resolves the repository from OpenCode's
worktree context before creating OpenTelemetry providers. It runs
`git remote get-url origin` and
`git rev-parse --abbrev-ref HEAD` concurrently with short, bounded timeouts and
adds non-empty results to the provider Resource as
`vcs.repository.url.full` and `vcs.ref.head.name`.

Discovery is best-effort: unclassified or non-Git worktrees, missing Git, a
missing `origin`, or command failure do not prevent telemetry startup. Values
configured explicitly in `otelExporter.resourceAttributes` take precedence per
key. The remote value is the exact trimmed Git output, and detached HEAD is
reported as `HEAD`.
When both VCS keys are configured explicitly, automatic discovery is skipped.
Offline replay and report commands do not probe the current working directory;
they only use Resource attributes supplied by their own configuration or data.

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENT_ENABLE_TELEMETRY` | Master switch for telemetry | `false` |
| `OTEL_SERVICE_NAME` | OpenTelemetry service name | `opencode` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint URL | - |
| `OTEL_EXPORTER_OTLP_HEADERS` | OTLP headers (comma-separated key=value) | - |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Metric aggregation: `delta`, `cumulative`, or `lowmemory` | `delta` |
| `OBSERV_OPENCODE_OTEL_ATTRIBUTES` | Span/event/metric attributes, including `ant.username` and `ant.uid` | - |
| `AIVISION_ANT_USERNAME` | Dynamic `ant.username` value when top-level `dynamicUsername` is enabled | - |
| `AIVISION_ANT_UID` | Dynamic `ant.uid` value when top-level `dynamicUid` is enabled | - |
| `OTEL_RESOURCE_ATTRIBUTES` | Standard Resource attributes | - |
| `AIVISION_OTEL_RESOURCE_ATTRIBUTES` | AIVision Resource attribute layer | - |
| `OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES` | OpenCode Resource attributes, including `ant.agent.*` | - |
| `AIVISION_META_TRIGGER` | Optional external task attribution (`automation`, `interactive`, or `unknown`) on the shared OTel Resource; overrides every Resource attribute layer | - |
| `AIVISION_LOG_DIR` | Override directory for the plugin-owned log file | edition default |
| `OBSERV_OPENCODE_LOG_DIR` | Override directory for `observ-opencode.log` | edition default |
| `OBSERV_OPENCODE_STATE_DIR` | Override directory for plugin-owned turn-counter state | `~/.aivision/ocd/state` |
| `XDG_DATA_HOME` | Common-edition OpenCode data root; logs use its `opencode/log` child | `~/.local/share` |

Log directory priority is inline/standalone `logDir`, then `AIVISION_LOG_DIR`,
then `OBSERV_OPENCODE_LOG_DIR`, then the edition default. Common edition writes
`observ-opencode.log` beside OpenCode runtime logs; AIVision writes it under
`~/.aivision/ocd/logs`.

The inline/standalone `otelExporter.metricsTemporalityPreference` takes
precedence over the environment variable and is applied to the real OTLP
metric exporter. `delta` exports each collection interval independently;
`cumulative` exports totals since the provider started. Consumers must use the
matching aggregation when combining multiple intervals or resumed processes.

The production plugin persists only a per-session `turnCounter` high-water mark
and the last observed user message ID
under `~/.aivision/ocd/state`. This lets `/resume` continue `agentic.turn.number`
after a plugin restart without reading OpenCode history or depending on the
optional replay plugin. State filenames are derived from a SHA-256 digest of
the session ID; files are written atomically with owner-only permissions.

To attribute the whole OpenCode process/task, configure the optional Resource
field without adding a top-level `meta` object:

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

`AIVISION_META_TRIGGER` overrides the final value after edition, JSON,
`OTEL_RESOURCE_ATTRIBUTES`, `AIVISION_OTEL_RESOURCE_ATTRIBUTES`, and
`OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES` are merged. Values are trimmed and
must be `automation`, `interactive`, or `unknown`; an explicitly empty or
invalid value becomes `unknown`. With neither entry point configured, the Resource
contains `ant.meta.trigger=""`. The value is one process-start Resource snapshot shared by traces,
metrics, and events/logs. It represents attribution supplied by the external
caller, is not inferred from OpenCode or replay transcripts, and never creates
`agentic.turn.trigger` on Turn or child spans.

### Programmatic Configuration

```typescript
interface ObservOpencodeConfig {
  // Master switch
  enableTelemetry: boolean;

  // Service name
  serviceName: string;

  // Directory for the independent observ-opencode.log file.
  logDir: string;

  // Content logging controls
  log: {
    events: ContentLoggingSwitches;
    spans: ContentLoggingSwitches;
  };

  // OTLP exporter configuration
  otelExporter: {
    endpoint?: string;
    protocol: string;
    headers: Record<string, string>;
    attributes: Record<string, string>; // ant.username, ant.uid, custom signal dimensions
    resourceAttributes: Record<string, string>; // ant.agent.*, optional ant.meta.trigger, and other Resource dimensions
  };
}
```

### Content Logging Controls

Each switch controls whether specific content is included in telemetry:

| Switch | Default | Description |
|--------|---------|-------------|
| `userPrompts` | `false` | Record user prompt text |
| `systemPrompts` | `false` | Record system prompt / instructions |
| `modelOutputs` | `false` | Record model output text |
| `toolDetails` | `false` | Record tool call parameters |
| `toolOutputs` | `false` | Record tool execution results |

Generation spans use a per-attempt `gen_ai.input.messages` snapshot. The first
call in a turn contains the current user message. A proven retry after an error
with no intervening assistant, reasoning, or tool output reuses that same
snapshot byte-for-byte; ordinary tool-loop continuations keep the existing
assistant/tool delta behavior. Privacy mode exports `[REDACTED]` without
changing those call boundaries. Diagnostic `api_error` spans copy input only
when the current request is known and otherwise omit it instead of emitting a
fabricated empty array.

Example with content logging enabled:

```json
{
  "plugin": [["observ-opencode", {
    "enableTelemetry": true,
    "otelExporter": {
      "endpoint": "http://localhost:4318"
    },
    "log": {
      "spans": {
        "toolOutputs": true,
        "modelOutputs": true
      }
    }
  }]]
}
```

## Trace Structure

```
openclaw.chat (root span, SERVER kind)
└── agentic.turn (per message→reply cycle, CLIENT kind)
    └── gen_ai.tool.{name} (per tool call, INTERNAL kind)
```

### Span Attributes

#### Root Span (`openclaw.chat`)
- `gen_ai.conversation.id` - Session ID
- `ant.username` - User identifier
- `ant.agent.type` - Agent type (e.g., "opencode")

#### Turn Span (`agentic.turn`)
- `ant.turn.id` - Turn identifier
- `gen_ai.usage.input_tokens` - Input token count
- `gen_ai.usage.output_tokens` - Output token count
- `gen_ai.response.model` - Model ID

#### Tool Span (`gen_ai.tool.{name}`)
- `gen_ai.tool.name` - Tool name
- `gen_ai.tool.call.id` - Tool call identifier
- `tool.input` - Tool input (if logging enabled)
- `tool.output` - Tool output (if logging enabled)

## Development

### Build

```bash
npm run build
```

### Type Check

```bash
npm run typecheck
```

### Clean

```bash
npm run clean
```

## Integration with Langfuse

This plugin is designed to work with Langfuse or any OTLP-compatible backend:

1. **Langfuse Self-Hosted**: Set endpoint to `http://your-langfuse:4318`
2. **Langfuse Cloud**: Use the OTLP endpoint provided by Langfuse
3. **Other Backends**: Works with Jaeger, Zipkin, Grafana Tempo, etc.

### Langfuse Configuration

For the common edition, set your deployment's OTLP endpoint and authorization headers using the [plugin configuration](#configuration).

Editions built with embedded Langfuse settings use their packaged production profile by default. Set `OBSERV_OPENCODE_LANGFUSE_ENV=gray` or `ANT_GRAY=true` to select the packaged gray profile.

## Related Projects

- [openclaw/observ-openclaw](../../openclaw/observ-openclaw) - Reference implementation for OpenClaw
- [cchooks](../../claudecode/cchooks) - Rust hooks for Claude Code
- [cdxhooks](../../codex/cdxhooks) - Rust hooks for Codex

## License

MIT
