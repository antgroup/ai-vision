---
object_key: model-connection
topic: model-discovery
title: Model Catalog Discovery
---

# Model Catalog Discovery

## model-connection-model-discovery@20261021-0005

**Discover models from verified provider catalogs before saving a connection**

```yaml
record_id: model-connection/model-discovery@20261021-0005
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-21T00:05+08:00
decided_at: 2026-10-21T00:08+08:00
implemented_at: 2026-10-21T00:08+08:00
supersedes: []
related_records:
  - model-connection/resource-contract@20260823-0234
  - model-connection/credential-handling@20261021-0005
affected_objects:
  - http-api
  - security
canonical_doc: docs/api.md
```

### Background

Connection setup currently requires users to find a base URL, model identifier and API key.
The platform will offer provider presets, regional defaults, API-key links and model discovery.
Catalog discovery must not become an arbitrary server-side HTTP proxy or expose a saved key.

### Decision

- Add `POST /api/v1/model-connections/discover` with `space_uuid`, `endpoint`, optional `api_key`
  and optional `connection_uuid`. Return `items: [{id}]` and `total`, without saving a connection
  or changing its availability status.
- The platform authorizes `MODEL_CONNECTION_MANAGE` and supplies the authorized Space. AgentLens
  only reads a saved credential using `get_in_space`. Missing/out-of-Space UUIDs fail with 404.
  A saved key can be reused only when the normalized endpoint matches the saved endpoint.
  Endpoint normalization trims surrounding whitespace and trailing slashes.
- Only the exact HTTPS bases maintained in `infra/llm_judge/model_discovery.py` can be discovered.
  The allowlist covers OpenAI, Gemini's OpenAI-compatible API, DeepSeek, Groq, SiliconFlow China,
  Mistral, OpenRouter, NVIDIA hosted API, Anthropic and Qwen China/Singapore. Endpoint selection fixes the catalog URL and
  optional filter parameters; the browser cannot supply request headers, paths or query parameters.
- Qwen uses the same region's native `/api/v1/models` catalog with text generation/inference
  filters. Anthropic uses native `/v1/models`, `x-api-key` and `anthropic-version: 2023-06-01`,
  with `has_more`/`last_id` cursor pagination. Other providers use OpenAI-compatible `/models`. Catalog metadata excludes known
  non-chat/non-text models when available; a listed model is not a successful diagnosis probe.
- Requests use bearer authentication except Anthropic's native catalog, verified TLS, no redirects, no environment proxies and no
  automatic retries. Network-phase timeout is 10 seconds. Streaming elapsed time is checked
  against 10 seconds between reads; this is not an absolute cancellation deadline.
- Response bytes across pages are limited to 2 MiB. Compressed responses are rejected after
  requesting identity encoding. Qwen stops at its reported total, an empty page, 20 pages or
  2,000 rows. Anthropic has the same page/row/byte/time bounds and rejects missing, invalid,
  credential-bearing or repeated cursors. Other catalogs return at most the first 2,000 rows. IDs are distinct nonempty strings
  of at most 256 characters, excluding control characters and the provided credential.
- Failures return static messages and stable `discovery_error` values. Upstream response bodies
  and exception text are never returned or logged. Provider authentication failures use HTTP
  502 so they cannot trigger a platform-session logout.

### Unchanged

Custom endpoints remain supported for saved connections and manual model entry. Existing
connectivity probes still perform a real Chat Completions request and determine availability.
No storage migration or provider column is introduced. Anthropic's official OpenAI compatibility
transport is covered by the separate sampling, thinking and connectivity-probe decisions.

### Impact and risks

Catalogs may include models that reject particular diagnostic parameters. Users still need the
existing connectivity test. Removing the new route only removes discovery; stored connections
remain usable. Provider catalog changes require updating the reviewed endpoint map and adapters.

### Alternatives

Browser-direct discovery introduces CORS differences and does not support a saved write-only key.
Arbitrary backend discovery would require a separate outbound-network policy and DNS-rebinding
defense. This change deliberately keeps discovery to reviewed provider catalog addresses.

### Validation

Offline route and HTTP transport tests cover authorization boundaries, credential binding,
endpoint allowlisting, redirects, provider errors, malformed/oversized responses, IDs, Qwen
pagination, elapsed time and unchanged state. Authenticated provider requests and live database
integration are separate acceptance checks, not established by these fixtures.

### Implementation notes

The additive route, bounded HTTP client and platform proxy are implemented in the current
worktree. Design and code review accepted this scope under the requested setup improvement.
Offline route, transport and proxy validation passed, including a slow single-byte stream
regression. Database-backed tests without a configured isolated database are skipped.
The user's follow-up added NVIDIA and Anthropic to this uncommitted implementation. The reviewed
catalog map and native Anthropic pagination now cover both providers.
No authenticated provider or production acceptance is claimed.
