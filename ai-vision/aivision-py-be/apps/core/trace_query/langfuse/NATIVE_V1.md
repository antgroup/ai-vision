# Native tracing contract

`native.py` serves the selected Langfuse v1 tracing reads from AI Vision's SigNoz
telemetry. It does not contact a Langfuse server. The frozen request matrix and
upstream SDK commit are recorded in `native_v1_contract.json`.

The SDK baseline is Python 4.16.0: `api.trace`, `api.sessions`, and
`api.legacy.observations_v1` (including their asynchronous clients).
`api.observations` uses v2 in this SDK and is outside this contract. Python 3.15.0 is the separately pinned regression target;
compatibility with every 3.x release or JS/TS is not implied.

Configure the SDK with `public_key=AgentUUID`, `secret_key=SpaceKey`, and the AI
Vision base URL. OTLP writes, `auth_check()`, project lookup and reads use the
same identity. The SDK Agent project trace URL enters the existing browser login and
Space authorization flow. Existing READ keys and bearer SpaceKeys keep their
explicitly provisioned external Langfuse project when `upstream_project_id` is
configured; public reads and project discovery retain that upstream response and
query contract. AgentUUID:SpaceKey SDK credentials always select native telemetry,
even when the Space also has an external project. READ keys and bearer SpaceKeys
without a provisioned upstream project use their authorized Space's native data.
Personal native Space reads must specify `agent_uuid`
unless exactly one active Agent is available; otherwise the API returns HTTP 400
with an Agent selection message. Business Space credentials can still read the
whole Space without selecting an Agent. A legacy unbound Space project URL is
outside this routing contract.
Credentials for one Agent cannot select another Agent via query parameters.
After authentication, the OTLP entry point resolves the Agent's enabled SigNoz
DataSource and stamps its configured `agent_product_filter` into the legacy
`ant.agent.product` resource key indexed by the supported storage image,
overriding client duplicates. The Agent routing ID is not the storage filter.
A missing route or filter returns HTTP 503 before forwarding telemetry. Plain official SDK clients need no additional
OTel resource configuration to remain readable under their own Agent. The
collector still stamps and enforces the authoritative Space/user/Agent identity.
SDK application `user.id` is preserved separately as `langfuse.user.id` before
the collector strips untrusted identity aliases.

The deployment must set `AIVISION_NATIVE_TRACE_HISTORY_START` to a fixed ISO
UTC timestamp at or before its earliest retained trace. Persist this origin
across upgrades and move it earlier **before** importing older data. An absent
origin returns an explicit capability error; it does not silently select a
recent-days window. New deployments can use their initial deployment time.

The exact supported SigNoz image (ob-labs/signoz commit
`1deab460854209ca0b18094d10128dcd0598aea9`) applies an inner 31-day query guard
in `pkg/modules/aivision/native.go:513`, independently of its outer configurable
93-day guard. Reads enumerate contiguous `[from,to)` windows of at most 31 days
from the fixed history origin to request time plus
`AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS` (default 300, range 0..86400). They
validate every storage page/detail and merge same-trace span fragments before
applying v1 filters, ordering and pagination. The default
`AIVISION_NATIVE_TRACE_MAX_WINDOWS=120` and 10,000 unique-trace limit fail
explicitly rather than returning partial history. Operators can increase the
window capacity deliberately; the history origin never slides automatically.
Targeted session details select candidate trace IDs by `session_id` in each
window. Their details and span-count validation omit that selector to retain
SID-free ancestors and children. Candidate trace IDs are also read in remaining
history windows, so a session attribute on only one fragment cannot truncate
the joined trace. Session lists still enumerate complete history before public
filtering and pagination. A future storage provider with indexed observation
reads or authorized history bounds can replace this mechanism without changing
the public wire contract.

`semantics/observations.py` and `semantics/usage.py` are shared with native query
and UI responses. Explicit standard attributes win per field; raw attributes
and normalization provenance are retained. Canonical token input includes
cache and output includes reasoning. Langfuse's flat named buckets are
exclusive; provider nested usage details are inclusive. The v1 `usageDetails`
projection preserves SDK flat bucket names and converts other canonical totals
back to exclusive buckets. Our derived standard attributes are recomputed from
preserved raw values on read; explicitly reported OTel attributes still win.
A complete flat Langfuse input/output/total map that balances its known exclusive
buckets treats absent optional cache/reasoning categories as zero. This proof
uses the raw map independently of per-field OTel overrides. Malformed,
conflicting or unrecognized raw buckets cannot prove absent categories are zero.
Incomplete maps require known cache buckets before deriving inclusive input and known
reasoning before deriving inclusive output. Otherwise those inclusive fields
and estimated cost remain unknown; the reported total and original exclusive
SDK buckets are still retained. OTel-only missing cache counters remain unknown.
Missing usage remains null natively and absent in `usageDetails`; only the mandatory legacy
v1 `usage.input/output/total` integers use the upstream zero placeholder.

## Model prices

`AIVISION_MODEL_PRICES` is a JSON array. Each entry names an exact provider and
model, USD rates per `unitTokens`, and an effective timestamp/version:

```json
[{"model":"private-model","provider":"private-provider","currency":"USD","unitTokens":1000000,"input":"2.50","output":"10","cacheReadInput":"0.25","cacheWriteInput":"3","version":"2026-10-01","effectiveFrom":"2026-10-01T00:00:00Z"}]
```

Prices are evaluated at the observation's start time; the newest effective
entry is selected. Decimal arithmetic preserves exact results. Reported costs
win. Unknown model/provider, missing cache counters for a cache-priced model,
overlapping buckets, invalid prices and missing usage produce a null cost and
an explicit reason. Price version/effective time are included in native output.
Agent/chain rollups are excluded from totals, duplicate span IDs are counted
once, and distinct retry spans count separately. Ambiguous nested generations
return unknown totals rather than guessing whether they are cumulative.

## Regression tests

```sh
AIVISION_ENV=test .venv/bin/python manage.py test \
  apps.core.tests.test_native_langfuse_api \
  apps.core.tests.test_langfuse_ingest_mappings \
  apps.core.tests.test_langfuse_onboarding.LangfuseOtelCompatibilityTests \
  apps.core.tests.test_trace_projection \
  apps.core.tests.test_trace_usage_semantics --noinput
```

The contract tests mock only the storage boundary. Release acceptance also
requires real official SDK writes/readback against the local collector/store,
a locked Python 3.x protocol regression, and authenticated trace URL browsing.
