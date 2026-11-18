# Hermes 0.21.5 compatibility

Audited upstream: `NousResearch/hermes-agent`, tag `v2026.9.24`, commit
`f97608f178d1ffeca59860195ab7da295f7c8e5f`; its native CLI reports `0.21.5`.
This audit supplements the retained [0.18.2 observer fixture](HERMES_V0_18_2_HOOK_CONTRACT.md).
It does not replace the older runtime contract or imply that all intermediate
Hermes releases have been exercised.

## Auxiliary calls after a turn ends

Upstream `agent/auxiliary_hooks.py` emits `pre_auxiliary_call` and
`post_auxiliary_call` once per provider attempt. Both carry native
`session_id`, `turn_id`, `api_request_id`, `aux_task`, and `retry_count`.
Production and replay register these hooks only when the host's `VALID_HOOKS`
exposes them; Hermes 0.18.2 keeps its original hook registration.

For custom endpoints, `agent/title_generator.py` defers its title request
until the primary turn settles. An isolated native process observed this order:

1. `pre_llm_call` establishes a native turn.
2. `on_session_end` closes that turn.
3. `on_session_finalize` closes the foreground session lifecycle.
4. `pre_auxiliary_call` / `post_auxiliary_call` carry the original session and
   turn IDs with `aux_task=title_generation`.

Core 0.2.2 retains at most 512 observed turn identities, without copying the
foreground user or assistant messages into that identity cache. Auxiliary requests require an exact session/turn match and
use independent pending state. They can parent their real LLM span to a closed
turn without reopening it, inventing a CHAT span, or superseding another turn.
The LLM span's `gen_ai.agent.name` preserves the host's `aux_task` value.
Unknown/evicted/turnless parents remain unassociated and emit a diagnostic.
Process shutdown clears this registry without inventing terminal callbacks.

The separate replay recorder captures these raw callbacks, including their
native identity, output and error. Both products use the same semantic reducer.
Full and privacy mode regression coverage includes the deferred lifecycle;
privacy preserves `error: null` as absence, not a truthy content marker.

Native acceptance uses the host's official one-shot `--usage-file` lifecycle
when available. `hermes_cli/oneshot.py` calls `wait_for_title_upgrades` before
writing that ledger; ordinary `chat -q` can exit while the daemon title thread
is still making a request. The ledger supplies native session/completion
identity; it is not a substitute for provider/OTLP evidence.

## Native SDK response objects

The main `post_api_request.assistant_message` is a native `NormalizedResponse`.
Its `ToolCall` entries store `name` and `arguments` directly; `function` is a
compatibility property. The raw recorder projects the public object fields,
including the normalized `Usage` counters, to JSON without parsing `repr` or
expanding arbitrary object internals. Semantic normalization then maps these
facts to assistant messages with `tool_calls[].function`, preserving native
call IDs, arguments, text and reasoning. Existing dictionary callbacks keep
their shape. Full/privacy tests cover live and replay with the same inputs,
and an installed-runtime test uses the official transport classes.

Auxiliary Responses requests retain the explicit `system_prompt` field;
`request.body.instructions` is also read when that content is available.
Privacy mode preserves the explicit prompt's redaction marker consistently
between live and replay processing.

## Source limits for auxiliary usage

`agent/auxiliary_hooks.py::_usage_summary` normalizes provider usage and deletes
`raw_usage` before dispatch. `agent/usage_pricing.py::normalize_usage` fills
missing buckets with zero. On the exact revision above, these two distinct
provider facts yield the same observer usage object:

```python
{"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
{"total_tokens": 0}  # Input/output buckets absent.
```

Both become `input_tokens=0`, `output_tokens=0`, cache/reasoning buckets zero,
`prompt_tokens=0`, `total_tokens=0`, and `request_count=1`. A missing entire usage
object produces `usage=None`. Auxiliary calls pass through `relay_llm`, not the
main-call `llm_execution` middleware, so the existing raw-counter middleware
cannot recover that field presence.

The plugin therefore preserves positive auxiliary counters and marks ambiguous
zero buckets missing. Unknown numeric attributes are omitted from both spans
and API events, including cancellation terminals; the missing marker remains.
Explicitly reported main-call zero retains the numeric value `0`.
Streaming auxiliary hooks also deliver the stream before consumption and have
no terminal usage. Main calls retain their existing middleware-based distinction
between explicitly reported zero and missing usage. A strict provider-to-span
zero-usage check can consequently remain unverified for auxiliary calls; this
upstream limit must not be hidden by changing telemetry or the oracle.

The native acceptance harness labels the single bounded fake-provider zero case
`hermes.host.auxiliary_zero_reportedness_unavailable`. It requires the exact
version, all three physical calls, an independently validated raw callback hash
chain and matching native/HTTP/span/API-event identities, content and usage.
Only the title call's expected usage is projected to unknown for a second
structural verification. Strict provider reconciliation remains false, and
the original evidence is unchanged.
