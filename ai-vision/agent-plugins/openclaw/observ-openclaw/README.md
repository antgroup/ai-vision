# OpenClaw Observability Plugin

OpenTelemetry-based observability plugin for [OpenClaw](https://github.com/openclaw/openclaw) AI agents with semantic conventions for AI Agent tracing.

## Features

- **Complete Event Coverage**: Captures ALL 25 OpenClaw plugin hooks
- **Hierarchical Tracing**: Connected spans for request → agent → tool lifecycle
- **Semantic Conventions**: Follows OpenTelemetry GenAI semantic conventions
- **Token Tracking**: Input/output tokens, cache read/write, model info
- **Tool Execution**: Individual spans for each tool call with duration
- **Session Management**: Full session lifecycle tracing
- **Subagent Support**: Traces for subagent spawning, delivery, and termination

## Trace Structure

```
openclaw.chat (CHAT span - root)
├── gateway.start
├── session.start
├── message.received
├── agent.{agentId} (AGENT span)
│   ├── llm.{provider}.{model} (LLM span)
│   │   └── gen_ai.usage.* (tokens)
│   ├── tool.{name} (TOOL spans)
│   │   ├── tool.duration_ms
│   │   └── tool.result_chars
│   └── ...
├── message.sent
├── session.end
└── gateway.stop
```

## Installation

### 1. Build the Plugin

```bash
cd observ-openclaw
npm install
npm run build
```

### 2. Configure OpenClaw

Add the plugin to your `openclaw.json`:

```json
{
  "plugins": {
    "load": {
      "paths": ["/path/to/observ-openclaw"]
    },
    "entries": {
      "observ-openclaw": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true
        },
        "config": {
          "otelExporter": {
            "endpoint": "http://localhost:4318"
          },
          "serviceName": "openclaw",
          "collectionMode": "privacy"
        }
      }
    }
  }
}
```

External plugins need the host's `hooks.allowConversationAccess` permission to
receive the conversation lifecycle hooks that create traces. This permission
belongs beside `config`, including in privacy collection mode; the observer
still redacts exported content according to `collectionMode`. Managed
`aiv connect openclaw` sets it for this observer only, and preserves an explicit
denial with an actionable error. Restart an already running Gateway after
changing the registration.

### 3. Restart OpenClaw

```bash
# Clear plugin cache
rm -rf /tmp/jiti

# Restart gateway (if running as service)
openclaw gateway restart

# Or restart your OpenClaw session
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `otelExporter.endpoint` | string | — | OTLP endpoint URL (e.g., `http://localhost:4318`) |
| `serviceName` | string | `"openclaw"` | OpenTelemetry service name |
| `otelExporter.headers` | object | `{}` | Custom headers for OTLP export |
| `otelExporter.attributes` | object | `{}` | Common span/event/metric attributes, including `ant.username` and `ant.uid` |
| `otelExporter.resourceAttributes` | object | `{}` | Resource attributes, including `ant.agent.*` metadata |
| `traces` | boolean | `true` | Enable trace export |
| `collectionMode` | string | `"full"` | `full` records current-turn content; `privacy` redacts content |
| `dynamicUsername` | boolean | `false` | Resolve `ant.username` from `AIVISION_ANT_USERNAME` whenever telemetry is recorded |
| `dynamicUid` | boolean | `false` | Resolve `ant.uid` from `AIVISION_ANT_UID` whenever telemetry is recorded |
| `businessAppId` | string | — | Business application ID for semantic conventions |
| `businessScene` | string | — | Business scene (e.g., `chat`, `training`, `evaluation`) |

## Environment Variables

### observ-openclaw runtime plugin

`observ-openclaw` owns live hook-to-OTel conversion and HTTP export. Transcript persistence, local replay output, and the replay/report CLI are owned by the sibling [`openclaw-replay`](../openclaw-replay/README.md) plugin so both live and offline paths reuse this package's `createHooks()` implementation.

| Variable | Description |
|----------|-------------|
| `AGENT_ENABLE_TELEMETRY` | Master switch for telemetry collection |
| `AIVISION_LOG_DIR` | Override directory for the plugin-owned log file |
| `OBSERV_OPENCLAW_LOG_DIR` | Override directory for `observ-openclaw.log` |
| `OPENCLAW_STATE_DIR` | Common-edition runtime state root; default plugin log directory is its `logs` child |
| `AIVISION_COLLECTION_MODE` | `full` or `privacy` |
| `OBSERV_OPENCLAW_OTEL_ATTRIBUTES` | Comma-separated span/event/metric attributes, including `ant.username` and `ant.uid` |
| `AIVISION_ANT_USERNAME` | Dynamic `ant.username` value when `dynamicUsername` is enabled |
| `AIVISION_ANT_UID` | Dynamic `ant.uid` value when `dynamicUid` is enabled |
| `OTEL_RESOURCE_ATTRIBUTES` | Standard comma-separated Resource attributes |
| `AIVISION_OTEL_RESOURCE_ATTRIBUTES` | AIVision Resource attribute layer |
| `OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES` | observ-openclaw Resource attribute layer, including `ant.agent.*` |
| `EDITION` | Build-time edition selector: `common` or `aivision` |

### OpenTelemetry exporter variables

| Variable | Description |
|----------|-------------|
| `OTEL_SERVICE_NAME` | Service name override |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint URL |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | OTLP protocol |
| `OTEL_EXPORTER_OTLP_HEADERS` | Headers as `key1=value1,key2=value2` |
| `OTEL_RESOURCE_ATTRIBUTES` | Resource attributes as `key1=value1,key2=value2` |
| `AIVISION_OTEL_RESOURCE_ATTRIBUTES` | AIVision-specific resource attribute overrides |
| `OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES` | observ-openclaw resource attribute overrides |
| `OBSERV_OPENCLAW_OTEL_ATTRIBUTES` | Common span/event/metric attributes as `key1=value1,key2=value2` |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Metrics temporality preference |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Traces-specific endpoint |
| `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | Traces-specific headers |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Metrics-specific endpoint |
| `OTEL_EXPORTER_OTLP_METRICS_HEADERS` | Metrics-specific headers |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | Logs/events-specific endpoint |
| `OTEL_EXPORTER_OTLP_LOGS_HEADERS` | Logs/events-specific headers |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metrics export interval in milliseconds |
| `OTEL_LOGS_EXPORT_INTERVAL` | Logs/events export interval in milliseconds |

### openclaw-replay plugin

Use the independent `OPENCLAW_REPLAY_CAPTURE_ENABLED`, `OPENCLAW_REPLAY_SESSION_DIR`, `OPENCLAW_REPLAY_SESSION_NAME`, `OPENCLAW_REPLAY_COLLECTION_MODE`, `OPENCLAW_REPLAY_STDOUT`, and `OPENCLAW_REPLAY_DEBUG` settings. See [`openclaw-replay/README.md`](../openclaw-replay/README.md). Production `OBSERV_OPENCLAW_*` variables are ignored by replay; former replay/session aliases only trigger a migration warning.

`agentic.collection.mode` is always forced into resource attributes from the final `collectionMode`.

## Backend Examples

### Local OpenTelemetry Collector

```json
{
  "config": {
    "endpoint": "http://localhost:4318",
    "serviceName": "openclaw"
  }
}
```

### Dynatrace

```json
{
  "config": {
    "endpoint": "https://{env-id}.live.dynatrace.com/api/v2/otlp",
    "headers": {
      "Authorization": "Api-Token {your-token}"
    },
    "serviceName": "openclaw"
  }
}
```

### Grafana Cloud

```json
{
  "config": {
    "endpoint": "https://otlp-gateway-{region}.grafana.net/otlp",
    "headers": {
      "Authorization": "Basic {base64-credentials}"
    },
    "serviceName": "openclaw"
  }
}
```

### Jaeger (via OTel Collector)

```json
{
  "config": {
    "endpoint": "http://localhost:4318",
    "serviceName": "openclaw"
  }
}
```

## Captured Events

### Gateway Events
- `gateway_start` - Gateway startup with port
- `gateway_stop` - Gateway shutdown with reason

### Session Events
- `session_start` - New conversation session
- `session_end` - Session termination with message count
- `before_reset` - Session reset/clear

### Message Events
- `message_received` - Inbound message from channel
- `message_sending` - Outbound message preparation
- `message_sent` - Message delivery confirmation
- `inbound_claim` - Message claim processing
- `before_message_write` - Message persistence

### Agent Events
- `before_model_resolve` - Model selection
- `before_prompt_build` - Prompt construction
- `before_agent_start` - Agent turn start
- `agent_end` - Agent turn completion with tokens

### LLM Events
- `llm_input` - LLM request with provider/model
- `llm_output` - LLM response with usage metrics

### Tool Events
- `before_tool_call` - Tool invocation start
- `after_tool_call` - Tool execution result
- `tool_result_persist` - Tool result storage

### Compaction Events
- `before_compaction` - Memory compaction start
- `after_compaction` - Compaction completion

### Subagent Events
- `subagent_spawning` - Subagent creation
- `subagent_delivery_target` - Delivery target setup
- `subagent_spawned` - Subagent started
- `subagent_ended` - Subagent termination

## Span Attributes

The plugin uses the following OpenTelemetry semantic conventions:

### GenAI Attributes
- `gen_ai.provider.name` - Provider name (e.g., `anthropic`, `openai`)
- `gen_ai.request.model` - Model name
- `gen_ai.response.model` - Actual model used
- `gen_ai.usage.input_tokens` - Input token count
- `gen_ai.usage.output_tokens` - Output token count
- `gen_ai.usage.total_tokens` - Total token count
- `gen_ai.input.messages` - Per-provider-call input: current user on the first call, the same write-once snapshot for a proven retry, then only newly added assistant/tool messages for ordinary tool-loop continuations; `[REDACTED]` when `collectionMode: "privacy"`
- `gen_ai.output.messages` - Current-turn output messages; `[REDACTED]` when `collectionMode: "privacy"`

Standalone error fallbacks include input only when the first-call prompt is
known. If the error cannot be correlated to a concrete provider request, the
input attribute is omitted rather than represented as an empty request.

### Agent Attributes
- `gen_ai.agent.name` - Agent identifier
- `agentic.turn.id` - Turn/session identifier
- `agentic.runtime.name` - Runtime name
- `agentic.runtime.version` - Runtime version

### Business Attributes
- `business.app_id` - Application identifier
- `business.scene` - Business scenario

### Tool Attributes
- `gen_ai.tool.name` - Tool name
- `gen_ai.tool.call.id` - Tool call identifier
- `code.tool.name` - Code tool name
- `code.tool.exit_code` - Tool exit code

## Development

### Project Structure

```
observ-openclaw/
├── openclaw.plugin.json    # Plugin manifest
├── package.json            # NPM configuration
├── tsconfig.json           # TypeScript config
├── semconv/                # Semantic convention definitions
│   ├── attribute_groups.yaml
│   └── spans.yaml
├── src/
│   ├── index.ts            # Plugin entry point
│   ├── config.ts           # Configuration parsing
│   ├── telemetry.ts        # OpenTelemetry setup
│   ├── hooks.ts            # All hook handlers
│   └── otlp/               # Generated OTLP code
│       ├── otlp.ts
│       └── semconv/        # Span helpers & constants
└── dist/                   # Compiled output
```

### Building

```bash
npm install
npm run build
```

### Type Checking

```bash
npm run typecheck
```

## Troubleshooting

### Plugin Not Loading

1. Ensure the path in `plugins.load.paths` is absolute
2. Clear the plugin cache: `rm -rf /tmp/jiti`
3. Check OpenClaw logs for plugin errors

### No Traces Appearing

1. Verify OTLP endpoint is reachable
2. Check `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable
3. Ensure `traces: true` in config (default)
4. Check backend logs for received data

### Missing Token Data

Token usage is captured from the `llm_output` hook. Ensure:
- The agent completes normally (not crashed)
- The model returns usage information
- `agent_end` event fires properly

## License

MIT
