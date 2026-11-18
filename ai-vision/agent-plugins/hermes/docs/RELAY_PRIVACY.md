# Native Hermes Relay reporting privacy

The Hermes onboarding guide generates the existing OpenInference exporter and
one first-party `pii_redaction` component. It uses Relay `0.10.4`, within Hermes
`0.22.5`'s existing `>=0.10,<0.11` requirement; no source plugin is installed.
The source of truth is
[`hermesRelayPrivacy.ts`](../../../vfe/src/pages/onboarding/hermesRelayPrivacy.ts).

The component sanitizes **observability copies before subscribers and
exporters**. Real provider requests/responses and tool arguments/results retain
their original values. It does not modify already stored records. Browser
masking and access controls are separate concerns, and an original-event view
cannot recover values removed before export.

## Generated policy

- Common credentials are always included: bounded known key/token formats,
  authorization and cookie text, PEM private keys, and explicit known
  credential/header fields. This is deterministic matching, not a guarantee
  that every sensitive value is detected.
- Email/phone matching is optional. Email matching applies to supported
  canonical payload strings. Phone matching is limited to root-string payloads
  and known content positions, such as model messages, tool text, and compaction
  summaries. It deliberately excludes arbitrary identifier/metadata fields:
  Relay's unrestricted phone detector can corrupt UUIDs and hashes.
- Advanced exact JSON Pointer paths omit the **entire matched field**, including
  objects, numbers, booleans and arrays. They do not find the same value copied
  elsewhere or inside an encoded JSON string. The UI permits at most 20 unique
  paths, each at most 512 UTF-16 code units, and supports `~0` and `~1` escapes.
  Whole-payload omission, wildcard paths, control characters, invalid escapes
  and unpaired Unicode surrogates are rejected before a snippet can be copied.

Unknown nested business fields and phone numbers in unknown structures require
explicit omission paths. Do not put sensitive content into model/tool names or
other non-payload identifiers. User-selected paths can remove useful evidence;
inspect a representative task after changing them. Missing evidence remains
unknown rather than becoming zero or a successful diagnosis.

The common policy preserves JSON syntax when credentials occur inside encoded
tool-call arguments. It retains lifecycle IDs, default compaction outcomes and
measurements, token usage and non-sensitive fields. It omits known header maps;
this can remove non-sensitive headers alongside credentials.

## Apply and verify

### Inspect the received evidence

Enhanced user-input summaries highlight recognized replacement markers within
the corresponding explicit user message, including earlier messages when expanded.
Native and Langfuse summaries preserve their existing user-message selection
rules. Tool results, system messages and model output are not user input.

The selected input record has one optional marker-help control. Native tool
details and the execution process also highlight markers. When the selected
tool record explicitly declares omitted paths, a short configuration hint shows
their preview; expanding it reveals every declared path. Empty arguments alone
do not establish that a field was removed. Full received content and source
navigation remain available through the existing original-record controls.

Markers can originate in the original input or another component. Relay 0.10.4
does not emit a per-call redaction receipt or rule-hit counters, so the view does
not display an inferred execution status. No marker hint does not establish that
redaction is disabled: fields may be omitted, mask/hash strategies may be
unrecognizable, no rule may have matched, or content may be absent or unloaded.
Likewise, a producer's `agentic.collection.mode` is configuration rather than an
execution receipt; `full` can still filter credentials.

LangChain `PIIMiddleware` operates on actual conversation messages, unlike
Relay's observability-copy sanitization. A deterministic LangChain 1.4.3 runtime
probe confirmed that input redaction changes the message received by the model,
while earlier root and `before_model` inputs can still retain the original
synthetic value in Relay exports. Output and tool-result processing similarly
does not retroactively sanitize earlier nodes. Verify model-context processing
and telemetry coverage separately; a node name or placeholder is not a receipt.

Original mode hides these enhancements and continues showing exactly the
received telemetry, which may already be sanitized. The trace-list controls
**Hide inputs and outputs** / **Show inputs and outputs** only affect browser
display; they do not change collection policy or remove stored data. Actual
policy effectiveness for a known sample still needs a controlled canary test
such as the opt-in native acceptance below.

### Merge the generated policy

Use the generated UI fragment rather than maintaining a second TOML example.
Merge its exporter and privacy rules into their respective existing component
kinds. Do not append duplicate kinds or replace unrelated settings. Keep the
document's root `version = 1` and verify configuration precedence with
`hermes doctor`. All profiles in the Hermes process share this policy.

Restart Hermes after changing the effective file. Selecting an option in the
guide only changes the generated fragment; it is not an activation receipt.
SpaceKey authorization remains in the existing environment variable and is not
embedded in TOML or passed through payload redaction. The marketplace offers
only official Relay onboarding; collector source remains an engineering
reference rather than another connection option. Keep only one intended
exporter for each signal when merging configuration.

### Existing single-policy components

Relay rejects a `profiles` array combined with legacy component-root fields:
`mode`, `builtin`, `local`, `priority`, `input`, `output`, `mark`, `tool_input`,
or `tool_output`. Do not paste the generated array alongside those fields.
Review a migration first: move each old rule's backend/action settings into its
own profile, retain component-wide `version`, `codec` and `policy`, remove the
conflicting legacy root fields, and then merge the generated profiles without
dropping the old rules. Check profile priorities and validate before restart.

This is not a mechanical, behavior-preserving conversion when the old rule
disabled individual surfaces: every enabled profile covers all supported
sanitization boundaries, and the old surface flags cannot be retained in this
mode. Review the resulting coverage explicitly or retain the existing policy
until an appropriate migration is prepared. The guide never rewrites local
configuration files automatically.

Official reference:
[Relay 0.10.4 PII configuration and observability semantics](https://docs.nvidia.com/nemo/relay/v0.10.4/configure-plugins/pii-redaction/configuration).

## Opt-in acceptance

The following opt-in commands run separately from the source collector's default
test discovery. Relay is required only in the dedicated acceptance environment.
Use Python 3.11+ with `nemo-relay==0.10.4`, and Node with native
`--experimental-strip-types` support.

From `ai-vision/agent-plugins/hermes`:

```sh
make relay-redaction-policy PYTHON=/path/to/relay-env/bin/python

make relay-redaction-e2e \
  PYTHON=/path/to/acceptance-env/bin/python \
  RELAY_RUNTIME=/path/to/hermes \
  RELAY_SOURCE=/path/to/official/hermes-source \
  RELAY_OUT=/path/to/new-private-evidence-directory
```

The native runner also needs this repository's existing native-agent acceptance
dependencies (`langchain-core`, OpenTelemetry protobuf support, and `requests`).
It starts a deterministic loopback model, executes actual Hermes file tools,
and uses synthetic credentials and personal information. It never reads a real
provider key. `RELAY_SOURCE` is optional for an otherwise complete installed
Hermes environment. Test-only compression thresholds and host tool-redaction
settings are isolated from the user's Hermes profile.

To include real platform readback, pass `RELAY_TARGETS=/path/to/private.json`.
The private JSON needs `endpoint`, `agent_uuid`, `space_id`, `space_key` and the
existing framework verifier's authentication fields. Use a disposable Agent and
Space. Do not commit the target file, authentication state, request captures or
decoded telemetry. Check the report's individual assertions, not only process
exit status, and delete only the test-owned resources afterwards.

The policy suite proves local canonical sanitizer behavior. The native suite
separately checks actual Hermes execution, outbound OTLP and, when configured,
platform source-field reconciliation. Deterministic responses do not prove
external-provider behavior, every Hermes execution surface, complete physical
model-call coverage, or final-session export. Those limits remain explicit in
the original [integration acceptance record](RELAY_INTEGRATION.md).
