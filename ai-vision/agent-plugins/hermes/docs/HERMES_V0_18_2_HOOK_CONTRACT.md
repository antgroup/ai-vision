# Hermes Agent v0.18.2 Observer Contract

`observ-hermes 0.2.0` is pinned to Hermes Agent `0.18.2` and observer schema
`hermes.observer.v1`. The fixture is executable documentation, not a sample
invented by this repository:

```text
hermes/tests/fixtures/hooks/v0.18.2/observer_envelopes.json
```

Regenerate it only with Hermes `0.18.2` installed:

```bash
python3 scripts/capture_hermes_v0182_hooks.py
```

The generator imports Hermes' installed `PluginManager`, verifies
`OBSERVER_SCHEMA_VERSION`, verifies every expected hook is exposed, captures the
callback envelope after Hermes injects `telemetry_schema_version`, and records
source-file hashes in the manifest.

## Supported Callback Surface

| Hook | Runtime meaning | Stable data used by observ-hermes |
| --- | --- | --- |
| `on_session_start` | New session initialization | `session_id`, model, platform |
| `on_session_end` | End of one `run_conversation` call, including interruption | session/task/turn IDs, completion or interruption state |
| `on_session_finalize` | Actual session or shutdown boundary | session ID, platform, reason |
| `on_session_reset` | Session rotation/reset boundary | session ID, platform, reason |
| `pre_llm_call` | Coarse LLM turn boundary before provider requests | session/task/turn IDs and projected prompt context |
| `post_llm_call` | Coarse LLM turn boundary after provider requests | session/task/turn IDs and projected assistant context |
| `pre_api_request` | One provider request starts | opaque `api_request_id`, request metadata, start time |
| `post_api_request` | One provider request succeeds | response model, finish reason, real usage, duration |
| `api_request_error` | One provider request fails | error object, status/retry data, duration |
| `pre_tool_call` | One physical tool execution starts | native `tool_call_id`, tool name, session/task/turn/API IDs |
| `post_tool_call` | One physical tool execution ends | native `tool_call_id`, duration, status, result/error |
| `subagent_start` | Child agent is allocated | parent turn, child session and subagent IDs, role/goal |
| `subagent_stop` | Child agent terminates | parent turn, child session, role/status/summary/duration |

## Semantic Commitments

- `api_request_id` is opaque and is never parsed for semantics.
- API hooks, not the coarser LLM hooks, are the source for LLM spans, usage,
  request events, and API RED metrics.
- Native `tool_call_id` is primary. A local synthetic ID is only a compatibility
  fallback when a future or partial callback omits the field.
- `subagent_stop` does not provide `child_subagent_id`; pairing uses native child
  session data when present and reports ambiguity instead of guessing.
- Hook adapters accept forward-compatible `**kwargs`, project an allowlist, and
  isolate callback failures from the Hermes runtime.
- Production normalizes the callback and applies the shared `SemanticEngine`
  without replay persistence.
- The separate `hermes-replay` plugin records a versioned raw callback envelope
  before semantic normalization only after explicit enablement.
- Offline replay validates raw schema and checksum continuity, normalizes through
  `aivision-hermes-core`, and applies the same `SemanticEngine`; it does not call
  production hook adapters or load production exporter configuration.

## Unsupported Or Conditional Surfaces

The following are intentionally honest gaps for Hermes `v0.18.2`:

| Surface | Status | Reason |
| --- | --- | --- |
| MCP golden scenario | Conditional skip | Supported only when Hermes exposes a configured MCP call through `pre_tool_call` / `post_tool_call`; the latest golden run had no configured MCP server. |
| Skill golden scenario | Conditional skip | Supported only when Hermes exposes an installed skill as a physical tool call; the latest golden run had no installed skill. |
| API-error golden scenario | Release skip | The adapter supports `api_request_error`, but the real golden run did not mutate provider credentials or force an external failure without authorization. |
| Compaction | Unsupported | Hermes v0.18.2 exposes no observer compaction hook. History shrinkage must not synthesize compaction spans or events. |

## Normative Validation Targets

Generated replay files are validated against:

```text
docs/semconv/
weaver/policies/
weaver/weaver-guard
```

Release evidence is regenerated independently for `full` and `privacy` replay
roots. Pre-split canonical transcript provenance is not valid for the separated
production/replay products. Violations and unknown semconv attributes are
release blockers.

Current post-split evidence passes the installed `hermes-agent==0.18.2`
contract test. Full and privacy live/replay outputs each report zero violations
and zero semconv issues; both mode-specific golden comparisons pass 3/3 files.
