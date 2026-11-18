# Optional OpenShell plugin images

This extension is selected explicitly. Ordinary AgentLens images and the five-service
private delivery do not install an OpenShell SDK or start this process.

`Dockerfile.sandbox-plugin` accepts a native AgentLens base assembled from the current
central producer output. That base must already contain compiled
`agentlens.sandbox_plugin` modules. It adds only the pinned upstream packages in
`requirements-sandbox-plugin.txt`; project-owned source is never copied. Its import
check rejects an older base without the new compiled plugin. CPython 3.11 is the
current native runtime contract. Do not substitute a source-copying service image
and describe the result as a private native delivery.

```bash
# Run from agentlens/; the exact reviewed native image is an operator input.
docker build -f docker/Dockerfile.sandbox-plugin \
  --build-arg AGENTLENS_NATIVE_IMAGE='<current-native-image@sha256:...>' \
  -t '<optional-plugin-image>' .
```

The workload is a separate operator-approved image containing the unchanged native
worker at `/app/src/agentlens-rule-worker` and its compiled implementation. It must
support numeric UID/GID 1000, the read-only application path, and writable `/sandbox`
and `/tmp`. The plugin supplies an explicit operator policy with non-root execution,
hard-required Landlock, and empty network policies. It does not install packages or
mount its Docker socket into workloads. Source development can explicitly replace
`WORKER_COMMAND` with an absolute Python argv array inside the selected workload.

## Independent enhanced Rule worker

The optional source distribution also builds `Dockerfile.rule-worker`, separately
from the plugin and ordinary service images. Its `python-eval-v1` runtime includes
the hash-locked evaluation libraries in `requirements-rule-runtime.txt` and a
generated `rule-runtime-manifest.json`. The fixed entry point is
`/app/rule-runtime/enhanced-worker`; enhanced requests cannot override its argv.
Reference tests run through pytest in a separate bounded child process. The image
still supplies the legacy native entry point for compatible restricted rules.

The source plugin recipe `Dockerfile.sandbox-plugin.selfhost` requires both
`AGENTLENS_SOURCE_IMAGE` and `RULE_WORKER_IMAGE`. It copies the qualified inventory
from the worker, without adding its evaluation libraries to the plugin's service
environment. The source release workflow checks real imports and actual compiled
reference-test execution before emitting the optional catalog. A native-private
delivery must separately qualify its producer and artifacts; the older native
plugin recipe alone does not establish enhanced-runtime availability.

`AIV_SANDBOX_ENHANCED_IMAGE` selects the exact immutable allowed worker reference;
automatic setup supplies it from the pinned catalog. The plugin reads the fixed
inventory at `/app/rule-runtime/manifest.json`. Both the manifest and the actual worker image's
`io.aivision.rule-runtime.manifest-sha256` label must agree before the plugin
advertises `python_eval_v1`. No packages are installed by a rule, and enhanced
execution never falls back to the service environment. See the
[runtime contract](../docs/diagnoser/sandbox.md) for materials, results and budgets.

Start the optional process with the image's upstream Uvicorn entry point, on port
8091. A drained graceful SIGTERM can exit 143 because Uvicorn re-emits the signal
after lifespan cleanup; exit 137, OOM, or incomplete shutdown are failures. Exactly one
process owns each durable journal; a second owner is rejected. Required operator
configuration uses the `AGENTLENS_SANDBOX_PLUGIN_` prefix:

| Suffix | Value |
| --- | --- |
| `TOKEN_FILE` or `TOKEN` | Exactly one plugin Bearer secret; files are regular, absolute, bounded files, optionally ending in CRLF |
| `JOURNAL_PATH` | Absolute SQLite file in a persistent directory; preserve on uninstall |
| `IMAGE_ALLOWLIST` | JSON array of exact allowed workload image references |
| `POLICY_PATH` | Absolute operator-owned policy YAML/JSON path |
| `DOCKER_SOCKET` | Absolute Unix socket for trusted Docker readback; only GET requests are sent |
| `GATEWAY` | One fixed OpenShell gRPC gateway host:port |

Optional suffixes: `WORKSPACE` (default `default`), `WORKER_COMMAND` (JSON argv),
`GATEWAY_TOKEN`/`GATEWAY_TOKEN_FILE` (exclusive optional gateway bearer),
`TLS_CA`/`TLS_CERT`/`TLS_KEY`, `MAX_ACTIVE` (4), `LEASE_TTL_SECONDS` (300),
`JANITOR_INTERVAL_SECONDS` (10), `OPERATION_TIMEOUT_SECONDS` (10),
`READY_TIMEOUT_SECONDS` (60), `CLEANUP_TIMEOUT_SECONDS` (30), `MAX_INPUT_BYTES`
(8388608; 8 MiB), `MAX_OUTPUT_BYTES` (1048576), and `MAX_TIMEOUT_SECONDS` (300).
The plugin's `MAX_INPUT_BYTES` and AgentLens's `SANDBOX_MAX_INPUT_BYTES` both default
to 8 MiB and accept 1024–16777216 bytes, with a 16 MiB hard ceiling. The effective
input allowance is the minimum of the AgentLens setting, plugin setting and selected
worker image's qualified reader limit. Only an exact immutable image with verified
`io.aivision.sandbox.max-input-bytes` metadata can advertise a larger reader limit;
missing, mutable, unreadable or invalid image metadata retains the legacy 2 MiB
(2097152 byte) reader limit. Increasing one setting does not upgrade another
component or the worker image.

Both the complete UTF-8 create/execute envelope and final normalized worker stdin,
including its newline, must fit. Code, the full Trace, materials and expected JSON
share this allowance; inputs are never silently truncated. `POST /v1/executions/validate`
checks both representations before any lease is reserved. Health advertises
`execution_input_preflight`, `streaming_stdin`, the plugin setting as `max_input_bytes`,
and per-image allowances in `input_limits`. AgentLens requires preflight; update the
optional plugin alongside it.
OpenShell remains pinned to 0.1.6: serialized requests above its 1 MiB message limit
use the existing bidirectional RPC with 4 KiB stdin frames. These small frames satisfy
the per-message limit; streaming does not raise the independent total input allowance.

Reserve the selected workspace exclusively for one durable plugin journal. Other SDK/CLI clients
must not delete or replace plugin-owned names there. The default workspace is appropriate only on a
dedicated gateway. OpenShell 0.1.6 exposes name-based deletion without an atomic expected-ID/version
precondition; before/after identity checks cannot prevent concurrent administrator replacement.
Gateway launch-signing/session authentication remains an operator configuration.
Plaintext loopback test gateways do not establish production authentication.

`GET /health/live` is unauthenticated liveness. Every other route requires Bearer
authentication. `GET /v1/health` reports readiness, protocol/provider versions,
capabilities, `draining`, and `active_leases`. Active count includes every nonclosed
journal entry, including cleanup pending and interrupted attempts.

`POST /v1/admin/drain` atomically rejects new admissions, then returns the same health
shape with `draining=true` and `ready=false`. Existing operations and cleanup continue.
The flag lasts for the process lifetime. Uninstall must drain first, require a verified
zero active count, and verify clean process shutdown before removing its container.
An unverified count or cleanup failure prevents removal; preserve the journal.

Before a lease becomes ready, Docker readback must observe containers with its exact
immutable provider ID/name and record that daemon's `/info.ID`. Cleanup checks the
same daemon identity before and after absence readback. A responsive empty socket
for another daemon cannot prove cleanup; missing identity remains cleanup pending.

Create failures without a recoverable immutable provider ID remain cleanup pending.
If a later lookup finds the owned instance, the janitor deletes and verifies it. If
no identity can be established, the slot stays reserved for operator investigation;
absence is never inferred from a missing API response.

Local SDK/Docker source-process acceptance, when recorded, is separate evidence from
compilation, final image/layer audits, multi-architecture qualification, publication,
or production deployment. Use the central native producer and existing artifact
inventory/layer-audit workflow for a distributable candidate.
