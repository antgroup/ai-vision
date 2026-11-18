# Native Agent observability E2E

This suite starts official pinned Agent processes and loads the production
plugins through their supported extension mechanisms. Pi uses RPC, Hermes uses
the headless CLI and ACP cancellation, OpenCode uses its CLI/server protocol, and DSH uses its official native
runtime with the production Cordis plugin. Kimi Code uses its official CLI
and ACP cancellation, invoking the production `kimihooks` through native
Stop/Interrupt hooks. Test code never calls plugin
callbacks to manufacture acceptance spans.

| Agent | Native runtime | Cases |
|---|---|---|
| Pi | `@earendil-works/pi-coding-agent@1.0.2` | 8 |
| Hermes | `hermes-agent[acp]` 0.21.5, official release commit `f97608f178d1ffeca59860195ab7da295f7c8e5f` | 8 |
| OpenCode | `opencode-ai@1.18.34` | 9 |
| DSH 0.1 (`dsh01`) | `@deepseek-ai/dsh@0.1.7-rc.2` | 8 |
| DSH 0.2 (`dsh02`) | `@deepseek-ai/dsh@0.2.0-rc.2` | 8 |
| Kimi Code | `@moonshot-ai/kimi-code@2.1.1` | 8 |

Hermes is installed as an editable checkout of its immutable official release
commit, inside the private runtime directory. The 0.21.5 release is not published
on PyPI and deliberately rejects wheel builds. Installation requires Git. The driver checks the actual CLI
version before starting a task; its 0.18.2 compatibility contract remains covered.
For 0.21.5, the official one-shot `--usage-file` path waits for deferred title
requests and supplies the real session identity. Fake runs separately enable
`hermes-replay` in the isolated test home to capture native raw callbacks;
the recorder does not invoke callbacks or change production telemetry.

The eight cases are tool loop, two turns with session resume, tool error,
streaming, cancellation, absent usage, explicit zero usage, and cache usage.
The local OpenAI-compatible HTTP provider uses LangChain fake models; no model
API key or external model endpoint is required. It returns a final receipt only
after the real Agent executes its file tool and supplies the random receipt in
the next provider request. Wire responses, native results, exported OTLP spans,
logs and physical counters are separate evidence sources.

Hermes's fake multi-turn case also sends one actual `finish_reason=length`
response with reported reasoning content and usage. The pinned host must send
its official continuation request, finish that turn, and resume the session for
the second tool loop. Source verification and the Weaver gate require this path;
the shared inventory remains eight cases per Agent. NVIDIA runs do not require a provider to truncate.

The additional Hermes turn-numbering probe runs three user turns in three
fresh official CLI processes, resuming one native session. It requires names
`Turn 1`, `Turn 2`, `Turn 3`, checks that the internal length continuation
does not increment the ordinal, and reuses the normal per-request usage,
identity, timing and parentage oracle. CI runs it on all three platforms after
installing the pinned runtimes. Run it separately with:

```bash
make -C ai-vision/agent-plugins/tests/native_agent_e2e hermes-turn-numbering \
  RUNTIMES=/tmp/aivision-native-runtimes/runtimes.json \
  OUT=/tmp/aivision-hermes-turn-numbering
```

`OUT` must be a new directory; `report.json` retains the raw hook and OTLP
evidence plus individual checks. A separate compression probe runs the first
two user turns in an official ACP process, closes it, loads that native session
in a fresh process, then triggers automatic threshold compaction within the third
user turn:

```bash
make -C ai-vision/agent-plugins/tests/native_agent_e2e hermes-compression \
  RUNTIMES=/tmp/aivision-native-runtimes/runtimes.json \
  OUT=/tmp/aivision-hermes-compression
```

The isolated host config sets `compression.protect_first_n: 0` and
`compression.protect_last_n: 3` so the short conversation is compactable. After
the first process exits, the driver enables documented automatic compression
settings: `enabled: true`, `in_place: true`, `threshold_tokens: 3000`. The first
prompt contains background text; this probe's opt-in fake provider calculates
input usage from the messages received over HTTP, so the real host threshold
triggers after the third turn's first model call. The second process loads the
original session, reads `/context`, then submits the third user prompt.
Acceptance requires fewer raw hook and API history messages, the host's summary marker,
the first user prompt removed, the current prompt retained, and OTLP ordinals
`1, 2, 3` on roots and their children. The summary's native pre/post callbacks
must retain the third turn's session and turn IDs, and all physical requests,
including the summary, must match OTLP usage and input/output. The `/context`
command and internal length continuation must not
allocate another user turn. The report retains both process IDs, native load
provenance, and complete ACP request/response evidence. The driver never imports
host internals, alters the session database, or supplies an ordinal to the
production plugin.

Manual ACP `/compress` runs outside a user turn in this pinned host. Its
auxiliary hooks have empty session/task/turn IDs, so the collector cannot
attribute that call to a user turn. The automatic probe exercises compression
inside the host's real turn context; it does not waive missing physical calls
or invent identity for manual compression.
An idle wait also does not establish compression in this host: starting a turn
refreshes its activity timestamp before the idle-compaction check. The probe
therefore requires actual history changes from the threshold trigger.

All normal requests require unique native pre/post callbacks and exact start/end
times. The fixed host can continue its one wire-verified length response before
the native post callback; only that request may lack an independently verified
end time, and its ID is listed in `unverified_native_end_ids` in the report.

OpenCode adds a ninth `api-retry` case: the first actual primary HTTP request
returns 429 without usage, then the official runtime retries the same native
assistant message and completes the real file-tool loop. Source verification
requires separate failed/successful generations, correlated API events, one
error metric, and exact physical call/token counts. The full fake matrix has
49 cases; selecting `api-retry` explicitly requires `--apps opencode`. This
fixture does not run in NVIDIA mode.

OpenCode fake and NVIDIA cases run through an isolated official server. Completed
cases wait for the native final assistant message and generated title. A shared
cleanup boundary keeps the server alive for the same exporter batch period after
the last turn or acknowledged native abort; invocation timeouts retain bounded cleanup.
The one-shot CLI can exit before its asynchronous metric export finishes. The
oracle still requires every physical call and token metric from the actual OTLP
exports; the lifecycle wait does not create or substitute observations.

DSH covers two runtime lines through their selected exact release candidates.
Both use the shared `drivers.dsh` implementation and product `DeepSeekHarness`;
`ant.agent.type` remains `dsh`. Each pin has an isolated installation. In a
shared Space, registration reuses the product's Agent UUID for both pins;
per-case `ant.agent.instance`, session and exact time window isolate evidence.
Its canonical session JSONL is a host-owned source,
read independently alongside the physical HTTP/SSE responses and original
OTLP. The matrix defines acceptance intent for these versions; a configured
case does not by itself prove compatibility or change the plugin's capability
level. Other DSH versions and features require their own evidence.

For every fake DSH case, physical HTTP status must be 200. Normal responses
need the fixture's actual terminal finish after its write/flush; absent finish,
explicit incomplete completion or disconnection fails. A cancelled request has
no provider finish and must have a native user abort and aborted generation,
including when no assistant message was exposed. Available partial messages
must be marked interrupted. `AgentResult.output` joins the received logical
text blocks exactly, including whitespace; a resumed process may receive only
the canonical transcript's ordered tail. Each generation and root still binds
its complete available input/output independently to canonical facts.

`source_verified` means those native sources agree. `platform_verified` also
requires actual ingestion and API readback; a collector-only run leaves it
false. Unknown and explicitly reported zero are distinct in the strict provider
oracle. The two DSH host limitations below retain that failed distinction in
the report rather than claiming complete provider fidelity. Pi 0.80.x compatibility tests remain in the plugin;
the full usage-presence E2E pins 1.0.2 because the older host lacks the required
raw provider-stream hook.

## Kimi native source and SDK boundaries

Kimi's installer pins the official npm runtime and builds only the production
`kimihooks` binary for the runner OS; Rust/Cargo and a native C linker are required.
The driver reads the host-owned wire JSONL and session state, retaining raw text,
hashes and the original native hook stdin. Cancellation waits for an actual ACP
text update before sending `session/cancel`, then requires Interrupt, cancelled
session state and completed production dispatch. The recorder invokes the real
binary; it does not substitute replay for production telemetry.

Code 2.1.1 records context-message deltas and request metadata hashes/counts.
Those deltas are checked as deltas; they do not claim a complete provider input.
The current profile binding supplies the actual system prompt; it is collected
when content capture is enabled and checked against native request metadata.
Only unavailable system content uses `[UNAVAILABLE]`. The unavailable response
model is `unknown`; request model identity remains exact. Native output, tools, parentage, finish/cancel state,
physical request counters, logs and available usage are independently reconciled.

The official normal Stop hook runs after the native `step.end` is written and
before `turn.ended` is dispatched. Its root ends at that available step boundary;
the later turn event includes hook/export time and is not available to the live
observer. Interrupt runs after the terminal turn event, so cancellation uses its
actual `turn.ended` time. The oracle checks those distinct source boundaries
exactly, without extending LLM duration or adding a timing tolerance.

The SDK collapses absent and explicitly zero usage into the same all-zero record.
The production observer conservatively exports missing usage for that ambiguous
record. Only fake `kimi@2.1.1` / `zero-usage` may accept that exact availability
loss. Only its `cache-usage` case may accept the SDK's lost cache-write and
reasoning counts. Available input/output/cache-read remain exact. Both allowances
retain the original failed provider comparison and require complete physical,
native, signal and raw OTLP proof. Any other loss or changed version fails.
See [known integration boundaries](../../../../KNOWN_ISSUES.md).

## Bounded Hermes host limitation

Only fake `hermes@0.21.5` / `zero-usage` may accept the documented
[auxiliary usage-presence loss](../../../../KNOWN_ISSUES.md#ag-003hermes-辅助请求丢失零用量的报告状态).
Two primary calls must retain their explicitly reported zero counts. The single
non-streaming title request must have matching real pre/post auxiliary callbacks
with exact session/turn/request identities, a valid raw-envelope hash chain,
successful physical HTTP completion, and the upstream all-zero normalized
summary. Its unavailable reportedness remains unknown in spans and API logs.
The independent source view changes only that auxiliary expected availability;
all original requests, strict failures and exported OTLP remain intact.

This bounded result keeps `source_verified`, `provider_reconciled` and
`verified` false. It requires exact counts, token samples, native parentage,
model, input/output, events and original OTLP integrity before
`canonical_verified=true` / `accepted=true` can be recorded. A failed primary
zero check, missing callback, changed version, unrelated identity, extra loss
or platform mismatch fails. The native usage ledger does not replace physical
HTTP or callback evidence.

## Bounded DSH host limitations

Only fake `unknown-usage` and `cache-usage` for `dsh01@0.1.7-rc.2` and
`dsh02@0.2.0-rc.2` can return `passed_with_known_limitations`. The selected host's
Pi adapter replaces absent provider usage with canonical input/output/total
zero, and drops provider reasoning-token counts from canonical cache usage.
This is a host adapter boundary; the observer receives those canonical facts.
See [AG-002](../../../../KNOWN_ISSUES.md#ag-002dsh-固定版本的宿主-adapter-丢失-usage-报告状态与-reasoning-计数).

The raw provider requests/responses and strict `source.verified=false` and
`source.failures` remain intact. A separate canonical proof must establish every
available native usage field, model, turn, full input/output, system instructions,
finish reason, tool identity/arguments/result, root topology and actual HTTP/API
counter. Original OTLP must decode exactly to the saved spans/logs/metrics.
Agent token histograms count fresh input; L1 token histograms include cache.
Their sums and measurement counts must exactly match canonical facts, including
real zero measurements. API events must belong to the same generation and carry
the same available usage, model and turn. No failure-name allowlist is used.

The allowance requires exactly the recorded provider-to-canonical loss, the
exact runtime pin, both calls' terminal HTTP evidence and the real receipt chain.
Missing evidence, extra usage loss, incorrect values, a different scene/version,
or a telemetry or platform mismatch fails. It does not turn unknown provider
usage into verified zero, or treat lost reasoning as a reported zero.

Accepted cases report `provider_reconciled=false`, `source_verified=false`,
`verified=false`, `canonical_verified=true`, `accepted=true`, and the explicit
`known_limitations` record. Platform ingestion can independently have
`platform_verified=true` after exact API readback. A readback failure clears
`accepted` and fails the suite. Collector-only runs leave `platform_verified`
false. Platform and Weaver gates recompute the canonical proof from original
facts and reject declarations that do not match it.

`summary.json` separates `verified_cases`, `known_limitation_cases`, and
`failed_cases`. A complete matching fake matrix therefore has 42 fully verified
cases and seven accepted host limitations, with status
`passed_with_known_limitations`; a full 49-case `passed` requires provider fidelity
for every case. The runner exits zero for either explicit successful state and
nonzero for any failed/incomplete inventory. This describes the gate's contract,
not a claim that all hosted OS or platform jobs have completed successfully.

## Local commands

Use Python 3.12, Node.js 24 and Rust/Cargo with a native C linker. Build `pi/observ-pi`,
`opencode/observ-opencode` and `deepseek/observ-dsh` with their normal npm build
commands first. Install the suite dependencies and native runtimes into private
directories outside the checkout:

```sh
python -m pip install -r requirements.txt
make test
make install REPO=/absolute/path/to/repo RUNTIME_OUT=/private/path/native-runtimes
make collector REPO=/absolute/path/to/repo RUNTIMES=/private/path/native-runtimes/runtimes.json OUT=/private/path/fresh-cases
```

Every output directory must be fresh. Runtime settings, native workspaces and
fake provider secrets are isolated from the user's Agent settings. Generated
SpaceKeys, ingest tokens and dashboard cookies stay private and are redacted
from saved evidence.

For a full platform run, provide the same six official image digests documented
by [framework E2E](../framework_e2e/README.md) in
`AIVISION_E2E_PLATFORM_IMAGES_JSON`, with Docker, Go and Make available:

```sh
make docker-platform REPO=/absolute/path/to/repo RUNTIMES=/private/path/native-runtimes/runtimes.json OUT=/private/path/fresh-platform-run
```

The helper reuses the existing six-service deployment: OceanBase, SigNoz OB,
collector gateway, AI Vision backend, AgentLens and frontend. It mounts the
current backend source into the pinned backend image, registers a temporary
account and five Agent product identities through public
APIs, and mints Agent-scoped ingest tokens. A controlled server-side metrics data
item queries the actual
`agent.api_request.count` OTLP metric through the V1 metrics adapter using
explicit delta exports, a sum over one-second buckets and a sum reduction.
This also covers counters restarting when a native process resumes a session.
Event-row counts cannot satisfy
this check. Native/public trace pagination, exact spans/parents/IO/model/usage,
sessions, trace metrics, logs and authorization isolation are checked within
each case's exact time window. Cleanup removes only this run's owned resources.
Readback allows up to ten seconds for a valid, incomplete source inventory to
finish ingestion. Rejected requests, invalid pagination metadata, incorrect span
fields, duplicate records and unexpected IDs fail immediately; sanitized attempts remain
in the evidence so a delayed read can be distinguished from a broken query.

The backend reconciles a detail/count snapshot race with at most three reads,
only while the count grows and every previously returned span remains identical.
Transport errors, identity/content changes, duplicate IDs and invalid or shrinking
counts remain errors. This does not change the readback gate above. Detail evidence
records a fixed error category for recognized server errors without copying
arbitrary response messages.

## CI scope

`native-agent-e2e.yml` schedules the 49 collector cases and plugin/unit checks on
Linux, macOS and Windows. A separate Linux/macOS Intel Colima matrix deploys the
real six-service local platform and runs all 49 ingestion/readback cases. CI
uses fake models and generated local platform credentials; it does not accept
an external platform endpoint or NVIDIA key. Matrix configuration is coverage
intent, and successful hosted runs are required before claiming a tested OS.

For the selected DSH pins, a successful source matrix retains
`passed_with_known_limitations` with 42 fully verified cases and seven bounded
cases. The CI exit gate accepts this explicit status; it does not claim that
all 49 cases have reconciled provider usage completely.

The existing database harness is reused to run Django contracts on both
OceanBase and seekdb. Native trace storage is tested on OceanBase; the seekdb
job proves backend SQL/contracts and does not claim a seekdb native trace store.
Windows Docker is excluded. Screenshots are local review evidence; CI checks
interface data and saves sanitized evidence without taking screenshots.

A Linux Weaver job validates the original OTLP from all three source matrices.
It independently rechecks the complete fake49 inventory before invoking the
repository policies and requires exact signal/sample counts, zero policy
violations and zero registry issues. An empty input, skipped sample or policy
evaluation error cannot satisfy the gate even if the validator exits zero.
Locally, put the repository's `weaver-guard` binary on `PATH` and run
`make semantics OUT=/private/path/completed-fake49 SEMANTICS_OUT=/private/path/fresh-semantics`.

Fork pull requests run the source matrix. The Docker platform job requires
private GHCR package access and runs for same-repository PRs or manual dispatch;
a skipped Docker job does not establish platform acceptance.

An explicit local NVIDIA smoke can use `make live MODEL=... TARGETS=...` and,
when needed, `PROXY=http://127.0.0.1:13659`. Its key is supplied only through the
`NVIDIA_API_KEY` environment variable. The proxy permits at most 20 physical
HTTP attempts per rolling 60 seconds, including auxiliary calls, discovery and
retries, below the platform's 40 RPM ceiling. It is not part of CI or the fake49 acceptance gate. These
cases do not prove a complete subagent, provider retry, catalog, or diagnostics
matrix; those capabilities require separate evidence.

`LIVE_APPS` selects the explicit NVIDIA subset (default Pi, Hermes, OpenCode and
both DSH pins). Kimi is currently covered by fake-model native E2E only.
