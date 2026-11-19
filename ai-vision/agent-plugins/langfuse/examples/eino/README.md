# Eino through the official Langfuse callback

Run a real Eino Chain using the official OpenAI-compatible model component and
Langfuse v2 callback. The callback exports directly to AI Vision's existing
`/api/public/otel/v1/traces` endpoint using HTTP/protobuf, gzip, and Basic Auth.
There is no AI Vision-specific Eino callback or manually constructed telemetry.

The example fixes these published module versions in `go.mod` and `go.sum`:

| Module | Version |
| --- | --- |
| `github.com/cloudwego/eino` | `v0.9.22` |
| `github.com/cloudwego/eino-ext/callbacks/langfuse/v2` | `v2.0.2` |
| `github.com/cloudwego/eino-ext/components/model/openai` | `v0.1.13` |
| `google.golang.org/grpc` | `v1.83.1` |

Go 1.25 or later is required. The `/v2` suffix is the Eino callback's module
version; the old callback without `/v2` uses a different ingestion protocol.

## Add the callback to an existing application

The integration itself is small. For a short-lived command, given an existing
compiled Eino `runnable`, use the official callback at the call site. The caller
returns its output and error:

```go
handler, err := langfuse.NewHandler(ctx, &langfuse.Config{
    Host:      os.Getenv("LANGFUSE_HOST"),
    PublicKey: os.Getenv("LANGFUSE_PUBLIC_KEY"), // AI Vision Agent UUID
    SecretKey: os.Getenv("LANGFUSE_SECRET_KEY"), // AI Vision SpaceKey
})
if err != nil { return nil, err }
defer func() {
    cleanup, cancel := context.WithTimeout(context.Background(), 15*time.Second)
    defer cancel()
    if err := handler.Shutdown(cleanup); err != nil { log.Print("telemetry shutdown failed") }
}()
traceCtx := handler.StartTrace(ctx, langfuse.WithName("my-workflow"))
output, runErr := runnable.Invoke(traceCtx, input, compose.WithCallbacks(handler))
handler.EndTrace(traceCtx, "") // child output remains available on the root
cleanup, cancel := context.WithTimeout(context.Background(), 15*time.Second)
defer cancel()
flushErr := handler.Flush(cleanup)
return output, errors.Join(runErr, flushErr) // preserve the application error
```

Import `langfuse` from
`github.com/cloudwego/eino-ext/callbacks/langfuse/v2` and `compose` from
`github.com/cloudwego/eino/compose`. Use a fresh cleanup context after
cancellation. The rest of this directory is an acceptance harness for inspecting
actual framework output and stored traces; it is not required application code.
In a long-running service, create one handler at startup and share it across
requests. Start/end a trace per request; call `Shutdown` only after active
requests have finished during service shutdown, not at the end of each request.

## Run without a model account

Run from this directory:

```sh
make test
make build
```

Tests start local model and OTLP HTTP servers. They execute the real framework,
model component, tool, and callback, then inspect the callback's actual
gzip/protobuf requests. The model responses are controlled fixtures, not real
inference or a quality benchmark.

To send the same fixture scenarios to an AI Vision deployment, create a
Langfuse project through **Market → Langfuse → Go → Eino** and copy its connection
values into the environment. `LANGFUSE_PUBLIC_KEY` is the Agent UUID; `LANGFUSE_SECRET_KEY` is the
SpaceKey. Keep the values outside source control.
The wizard's downloadable `main.go` is a minimal application example. This
directory is the separate acceptance harness. Map the wizard's
`LANGFUSE_BASE_URL` to `LANGFUSE_HOST` here, and map `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, and `OPENAI_MODEL` to `MODEL_BASE_URL`, `MODEL_API_KEY`, and
`MODEL_NAME` for live runs. The public/secret key variable names stay unchanged.
Both examples pass the configured base URL explicitly to the callback's
`Config.Host`.

```sh
export LANGFUSE_HOST='https://your-ai-vision-host'
export LANGFUSE_PUBLIC_KEY='your-agent-uuid'
export LANGFUSE_SECRET_KEY='your-space-key'
make run-fixture LEDGER=/tmp/eino-fixture-ledger.json
```

The six scenarios are `normal`, `stream`, `missing_usage`, `error`, `cancel`,
and `shutdown`. Each is a separate trace. Successful runs execute an application
root, an Eino workflow, one model call, a ToolsNode, and an `echo` tool. Failed
and cancelled model requests stop before tool execution. `shutdown` deliberately
leaves the application root open until the callback drains pending telemetry.

The official callback records user cancellation with status `UNSET` and
cancellation details, while a model failure has status `ERROR`. Missing usage
must stay missing; a known zero count is a different value.

## Run with an OpenAI-compatible provider

Set the three model variables in addition to the AI Vision connection values:

```sh
export MODEL_BASE_URL='https://your-provider.example/v1'
export MODEL_API_KEY='your-model-api-key'
export MODEL_NAME='your-model-name'
make run-live LEDGER=/tmp/eino-live-ledger.json
```

The provider must support OpenAI-compatible chat completions, function tools,
forced tool selection, and streaming tool calls. Baidu Token Plan can use its
OpenAI-compatible base URL and a supported tool-calling model. The example makes
two model requests: one normal and one streaming. It asks each model to call
`echo` with the fixed text `hello-eino`; this is an integration check, not an
evaluation of model reasoning. Error and cancellation scenarios remain local
fixtures to avoid unnecessary live calls.
Each request has a 256-token output limit, an 80-second HTTP timeout, and a
90-second scenario deadline. The pinned model client does not automatically
retry these calls; the harness also checks that each scenario made exactly one
model HTTP request. Telemetry export retries have their own bounded policy.

The model's raw HTTP usage fields supply the ledger's token expectations. The
official callback independently converts the Eino model results into telemetry.
No estimates are substituted when the provider omits usage.

**Known upstream limitation:** callback `v2.0.2` omits counters whose value is
zero. A provider's explicitly reported zero output count can therefore become
missing telemetry. A separate local test demonstrates this with the real Eino
pipeline. The ledger retains the provider's explicit zero and the platform
verifier reports the difference; the example does not repair or conceal it.

## Verify stored traces

The runner waits for callback Flush/Shutdown before saving a versioned ledger
with private file permissions. A successful run confirms completion and export;
it does not by itself confirm Collector/SigNoz persistence or query behavior.
Use the platform read-back verifier from the Langfuse directory:

Create a private target file with the deployment's query identifiers. The
`space_id` and `agent_product` identify the containing Space and Agent type;
`agent_uuid` identifies the Agent created for this application. `space_key` is
the AI Vision SpaceKey, not the model API key.

```json
{
  "langfuse": {
    "endpoint": "https://your-ai-vision-host",
    "space_id": "your-space-uuid",
    "agent_product": "your-agent-product",
    "agent_uuid": "your-agent-uuid",
    "space_key": "your-space-key"
  }
}
```

```sh
node tests/eino/verify_platform.mjs \
  --targets /path/to/private-targets.json \
  --ledger /tmp/eino-fixture-ledger.json \
  --out /tmp/eino-platform-report.json
```

Repeat `--ledger` to verify fixture and live ledgers together. The command exits
with status 0 only when every check passes. Its terminal summary shows each
trace's expected and actual distinct span counts, returned row count, parent
anomalies with expected/exported/persisted parent IDs, and expected versus actual
known/billable call coverage and known token totals.

The JSON report preserves the check names in `results` and `failures`, and adds
the same per-trace information under `traceDetails`. Its `usage.expected` comes
from the independent ledger; `usage.actual` contains the platform's reported
coverage, complete totals, and known subtotals (`reported`), rather than values
recomputed from those expectations. Unknown token totals stay `null`; explicit
zero remains `0`. A known subtotal of zero with no known calls does not establish
zero usage. Failed reads show unavailable actual measurements, and an invalid
scenario ledger is marked invalid without a query. Reports include diagnostic
IDs and counts, but no prompts, responses, endpoint URLs, or credentials.

The ledger keeps independently defined business expectations separate from
observed export IDs and raw attributes. Expected parent relationships come from
the fixed workflow; token expectations come from provider responses. Observed
IDs and timestamps locate the corresponding stored data. This prevents a
platform response or export-normalization bug from generating its own expected
answer.

An HTTP 2xx response alone does not establish full OTLP acceptance. The harness
also checks `partial_success.rejected_spans`, fails on partial rejection, and
redacts receiver error messages before they reach exporter diagnostics.
Repeated successful deliveries are detected separately from failed attempts.

The callback uses per-invocation `compose.WithCallbacks`, so it does not register
global handlers. Inputs are synthetic, credentials are never printed, and
provider error bodies are replaced with a generic error before tracing. Use the
callback's masking facilities and your application's data policy before
adapting the example to private prompts or responses.

## Scope

This example validates one pinned Eino/OpenAI-compatible integration. It does
not claim compatibility with all Eino components, all model providers, legacy
Langfuse ingestion, embeddings, retries, or the full agent capability matrix.
The application root is a `chain` observation; the official callback labels
Eino's workflow and ToolsNode wrapper as `span`, model calls as `generation`,
and the actual tool call as `tool`. These source semantics are preserved.

Local acceptance on 2026-10-22 used Go 1.27.3 on macOS arm64. The Sonic dependency
fell back to Go's `encoding/json` on that toolchain. The six fixture scenarios
passed through Nginx, Django, Collector, SigNoz, and seekdb, followed by native
API read-back: 6 traces, 26 spans, and 289 checks. Two Baidu Token Plan calls with
`qianfan-code-latest` (normal and streaming) passed 108 read-back checks over
10 spans. Delivering each fixture's actual OTLP batch twice did not increase
the stored trace/span counts. Local tests separately cover explicit-zero loss,
OTLP partial rejection, diagnostic redaction, and duplicate delivery detection.
These are local acceptance results, not hosted CI or a model quality benchmark.

References: [official callback documentation](https://github.com/cloudwego/eino-ext/blob/callbacks/langfuse/v2.0.2/callbacks/langfuse/v2/README.md),
[Eino](https://github.com/cloudwego/eino), and
[OpenAI model component](https://github.com/cloudwego/eino-ext/tree/components/model/openai/v0.1.13/components/model/openai).
