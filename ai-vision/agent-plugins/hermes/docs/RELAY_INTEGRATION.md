# Official Hermes Relay integration acceptance

## Result and scope

On 2026-11-02, official Hermes `v0.22.5` executed three real user turns with
NVIDIA `nvidia/nemotron-3.5-lightning-30b-a3b`, real `read_file` calls and automatic
context compaction. Its official NeMo Relay OpenInference exporter sent telemetry
through AI Vision's Basic-authenticated `/api/public/otel/v1/traces` proxy into
an isolated SigNoz/seekdb deployment. Native trace APIs, v1 trace/observation
APIs and trace metrics returned the exported data. The production
`observ-hermes` plugin was not installed in this isolated Hermes home.

This verifies the automatic-compaction scenario and the exported telemetry.
It does **not** establish complete coverage of all physical model requests,
session shutdown, manual compression or every Hermes execution surface.
The independent report separates `scene_verified=true` from
`complete_provider_coverage=false`.

| Item | Actual tested identity |
| --- | --- |
| Hermes source | `v0.22.5`, commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e` |
| Annotated tag object | `56b6a31fededdf7364e1a0a79548d144e7fd81a3` |
| Hermes process | macOS/arm64, Python `3.14.3`, OpenAI SDK `2.24.0` |
| NeMo Relay | `0.10.4`; the upstream requirement is `>=0.10,<0.11` |
| Platform | Local Linux/arm64 containers, actual seekdb and SigNoz storage |
| Backend | Current checkout mounted over the cached backend image's application directories |
| Images | Cached `aiv-local010/*:0.1.0-daily.20261101-arm64` and `aiv-local010-deps/seekdb:approved-arm64`; exact image IDs retained in the local `platform-images.json` receipt |

The official source was unchanged. Its Python 3.14 dependencies were installed
with the overrides already declared in upstream `[tool.uv]`; resolving the
editable package without those overrides encounters its Pillow/browser-harness
constraint conflict. These results do not qualify new release images, Linux or
Windows native Hermes execution, Kubernetes, a cold installation or other model
providers.

## Observed data

The automatic run made eight physical chat requests: six primary calls, one
compression summary and one session title. All eight completed with HTTP 200.
Model discovery GETs are recorded separately, making 18 physical HTTP requests
in total. A successful receipt response alone is insufficient: an earlier model
response invented a receipt without calling the tool and was rejected by the
independent random-file assertion.

| Evidence | Result |
| --- | --- |
| Native user turns / actual file tools | 3 / 3 |
| Exported spans | 22: 7 physical LLMs, 7 logical wrappers, 3 tools, 3 turns, 2 session segments |
| OTLP batches accepted by the real platform | 3 / 3, HTTP 200 |
| Exported LLM usage | Input 44,270; output 764; total 45,034 |
| All physical chat request usage | Input 44,710; output 773; total 45,483 |
| Difference | The title request's 440 input / 9 output / 449 total tokens were not exported |
| Successful compaction | `llm_summary`, `turn_start_threshold`, `in_place_committed` |
| Compaction size | Estimated 4,999 → 1,611 tokens; 3,388 reclaimed |
| Compaction timing | 7,902 ms overall; 7,867 ms summary generation |
| Prior attempt | `compaction.attempt`, skipped with `insufficient_messages`; no committed rewrite claimed |

The committed event belongs to trace
`01a19bb565d87bf2a5533bf229ea2a4a`, turn span `a5533c01f8c62f05`.
All 22 exported span IDs, available source attributes, resource fields, parent
IDs, times and event attributes were read back. Compaction timestamps were
independently compared with the decoded original protobuf nanosecond fields.
OpenInference prompt/completion/total counts matched native API usage and
physical provider usage samples. Per-trace metric counts and usage were checked
against the exported inventory. API reconciliation covers those exported spans;
it does not silently include the unexported title request.

The compression marks contain `nemo_relay.mark.data.*` fields and promoted
`hermes.*` runtime metadata. The OTLP exporter does not include Relay's original
`data_schema` object; `hermes.relay.schema_version` describes the runtime
envelope and is not the compaction payload schema version.

## Product acceptance

Hermes's existing application dialog now defaults to the official Relay guide.
It generates the configuration from the selected Agent UUID and current Space,
keeps authorization in an environment variable, and retains a collapsed legacy
plugin guide. Existing plugin projects retain their dashboard until an Agent
manager explicitly selects the Relay collection path. This selection changes
only instance metadata; it preserves product identity, UUID, status and other
metadata. No navigation Tab was added.

The source frontend was checked against the real isolated backend and stored
telemetry. Its enhanced trace view showed the reported automatic-compaction
values, navigated to the original event and hid the enhancement in Original
mode. OpenInference user messages were reconstructed only from explicitly
reported role/content fields while retaining dedicated business-input priority.
The Relay instance dashboard loaded 17 model calls, 7 tools and 24 recent call
records across all live trials. These aggregate dashboard counts include earlier
diagnostic runs; the passing automatic scenario above remains a separate 22-span
inventory. The generated Basic authorization was checked against the exact Agent
UUID, and the legacy entry was exercised. Browser evidence used real APIs and
contained no mocked responses.

## Official configuration and remaining boundaries

Use the onboarding page's single OpenInference endpoint component. Hermes core
owns the runtime; do not also enable the removed
`observability/nemo_relay` plugin. Do not activate a second OTLP exporter through
ambient `OTEL_EXPORTER_OTLP_*` settings.

The accepted run also used Hermes's documented session segmentation in its
isolated `config.yaml`:

```yaml
gateway:
  telemetry:
    session_segments:
      on_compaction: true
      max_turns: 1
```

Merge only this telemetry section into an existing profile as appropriate.
Rotation closes the preceding session segment at the next user-turn boundary;
it does not change the conversation or its compression policy. Do not copy the
test runner's 8,000-token compression threshold, protected-history settings or
model settings into production onboarding.

The tested boundaries are:

- Without segmentation, completed turns export while the session root stays
  open. With segmentation, the first two session segments exported before the
  next turn. The final segment was still absent after a successful official
  `hermes gateway stop`. Upstream `gateway/run.py` uses `os._exit`, whereas
  `agent/relay_runtime.py` registers shutdown with `atexit`; this source evidence
  explains a possible shutdown gap and is not a completed upstream fix.
- Real manual `/compress` reduced history/request size, but its compaction mark
  and summary call did not appear in the captured OTLP. Automatic compression
  inside a live user turn exported both the summary and its committed mark.
  Manual and automatic results must remain separate.
- Ordinary exported spans did not supply a business user ID or conversation ID.
  The native session ID was available in compaction event data. Authentication
  provides routing identity; it must not be presented as the business user or
  used to invent session membership for other traces.
- OpenInference retained message roles/content and tool-result correlation IDs,
  output tool calls, tool spans and original response/usage fields. Historical
  assistant `tool_calls` were absent from its flattened input-message fields.
  This is not full provider-request fidelity.
- The old Hermes CommonDashboard consumes plugin traces/logs/metrics. Its empty
  queries cannot be used as acceptance for a traces-only Relay integration;
  framework trace queries and the selected product UI require their own checks.

Official references:
[native Relay and compaction contract](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/website/docs/developer-guide/relay-shared-metrics.md),
[plugin migration and session segmentation](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/website/docs/user-guide/features/built-in-plugins.md).

## Reproduce without local machine paths

Install the official pinned Hermes source with its supported Python 3.14
environment, ACP extra and declared upstream overrides/lockfile. Install the
[native harness requirements](../../tests/native_agent_e2e/requirements.txt)
in the Python environment running this verifier. The frontend's Node.js
dependencies must be present because the runner invokes the actual onboarding
TOML generator. It never copies the user's Hermes profile or installs the legacy
collector/replay plugins.

Create a private target JSON outside Git for an existing isolated platform:

```json
{
  "endpoint": "http://127.0.0.1:18080",
  "space_id": "TEST_SPACE_ID",
  "agent_uuid": "TEST_HERMES_AGENT_UUID",
  "agent_product": "Hermes",
  "space_key": "TEST_SPACE_KEY"
}
```

The test creates only a fresh private artifact directory and its isolated Hermes
home. It does not deploy a platform, create accounts, change existing profile
permissions or clean up shared platform resources. Supply `NVIDIA_API_KEY`
through the process environment, choose an available model explicitly and run:

```bash
make -C ai-vision/agent-plugins/hermes relay-native-e2e \
  PYTHON=/path/to/harness-python \
  RELAY_RUNTIME=/path/to/official-hermes \
  RELAY_TARGETS=/private/path/target.json \
  RELAY_OUT=/private/path/new-relay-run \
  RELAY_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

Add `RELAY_PROXY=http://127.0.0.1:PORT` only when a local HTTP proxy is needed.
The run opts into real NVIDIA requests and keeps the shared 20-attempts-per-minute
budget. It records actual usage for every physical request, including discovery
and auxiliary work. Credentials remain in memory; the native host receives a
disposable credential for the local forwarding server. No provider/model is
silently replaced with a fixture.

The runner's `--mode manual` is a separate diagnostic and does not count a
missing committed mark as a pass. To recheck a completed report against the
real platform without any additional model calls:

```bash
/path/to/harness-python \
  ai-vision/agent-plugins/hermes/tests/relay_native_e2e.py \
  --target-file /private/path/target.json \
  --verify-report /private/path/completed-run/report.json
```

Reports and native logs contain test conversation content and local paths and
must remain private. The original 2026-11-02 run retained the decoded original
protobuf JSON, not its original binary HTTP body. The checked-in runner adds
base64 and SHA-256 capture of the original incoming HTTP body for future runs;
it does not retroactively claim byte-level evidence for the original run.
