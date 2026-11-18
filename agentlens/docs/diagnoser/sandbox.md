# Optional sandbox execution

Sandbox is an optional integration capability. OpenShell 0.1.6 with its Docker driver is the first
provider. AgentLens works without a provider. E2B, Runloop and CubeSandbox are extension candidates,
not implemented providers. The base dependency set contains no OpenShell SDK, gRPC or Deep Agents
backend. Only the separately installed plugin environment/image includes provider dependencies.

## Ownership and deployment

AgentLens owns Space connections, policy resolution, immutable Task routing, attempt-owned leases
and result validation. Django authenticates users, enforces Space permissions and proxies safe DTOs.
The integration page configures connections and shows installation guidance. The operator installs a
separate plugin and an OpenShell gateway. The connection endpoint is the plugin's HTTP endpoint,
not the gateway's gRPC endpoint. Neither Django nor workloads receive the Docker socket.

The optional plugin app is part of the AgentLens package so private native builds compile its owned
modules. Installing its SDK in the optional image does not change the default five-service delivery.
See [deployment instructions](../../../docs/deployment/en/sandbox-plugins.md). Published images,
cloud providers, Kubernetes qualification and production rollout require separate validation.

## OpenShell ownership boundary

Use one durable plugin journal per exclusively operated OpenShell workspace. Do not run other SDK,
CLI, gateway clients or manual delete/recreate operations against plugin-owned names in that workspace.
The `default` workspace is suitable only when its entire gateway is dedicated to this plugin; otherwise
provision and select a separate workspace. Gateway administrators remain trusted infrastructure owners.

OpenShell 0.1.6 deletes sandboxes and templates by canonical name. Its public API has no expected-ID or
version precondition. The plugin rejects a replacement observed before deletion and verifies the
original identity afterward, but those checks cannot make the upstream delete atomic against an
external actor replacing the same name between requests. `expected_sandbox_id` in `wait_deleted` is
an observation fence, not a delete precondition. Do not describe this as protection against concurrent
gateway-administrator replacement. See the [ownership decision](../adr/sandbox-execution/lifecycle.md).

## Frontend hierarchy

The integration entry is **Plugins → Sandbox → OpenShell**. The Plugins directory lists capabilities;
Sandbox owns provider discovery and the Space default/require policy; OpenShell owns connection
management and secondary deployment guidance. Links preserve Space identity, and the previous
`/sandbox-connections` URL redirects with its query/hash intact.

[Plugins directory](../../../docs/assets/plugins/catalog.webp) ·
[Sandbox capability](../../../docs/assets/plugins/sandbox.webp) ·
[OpenShell provider](../../../docs/assets/plugins/openshell.webp)

## Connections and policy

The recommended OpenShell installation runs on the existing AI Vision deployment
host. Its browser drawer prepares a single `aiv plugin setup openshell` command;
users do not enter image references, gateway addresses, Docker paths or plugin
credentials. The host CLI checks the actual deployment and daemon, selects pinned
catalog artifacts, generates private configuration, and starts a dedicated mTLS
gateway and plugin. A missing installation catalog or an unsupported deployment
is reported before the command is prepared. Existing services retain the advanced
manual connection path.

Host progress does not establish a saved connection. Dedicated enrollment passes
the generated key to AgentLens without storing it in Django or returning it to
the browser. AgentLens registers a fixed deployment profile idempotently and tests
it from the real diagnosis network. The final **Set as Space default** action is
explicit, performs another readiness check, and binds only the unchanged verified
revision and credential. It preserves the existing sandbox requirement policy.
Source packaging is separate from private binary delivery and release qualification.

A connection contains provider, plugin endpoint, write-only plugin access token, approved workload
image, CPU, memory and timeout. Test checks authenticated readiness and the Custom Rule capability;
it does not create a workload. A successful Trial/task provides workload evidence. Secret rotation
preserves frozen identity; profile changes increment revision and reset readiness. Unverified leases
block profile changes and deletion. Connections referenced by bindings, diagnosers or active Tasks
cannot be deleted. Disable makes future execution fail closed.

Space bindings select a default connection and can require sandbox execution. Only Custom Rule is a
consumer in this version. Builtin rules and LLM Judge retain their existing execution behavior.

```json
{"sandbox": {"mode": "inherit", "connection_uuid": "optional-explicit-connection"}}
```

`inherit` uses the explicit connection or Space default, then local if none is selected. `required`
requires a selected connection. `local` requests the existing restricted local worker and is rejected
when the Space requires a sandbox. Missing, disabled or unavailable selected connections never
fall back to local. User specs cannot supply endpoints, credentials, gateway policy or executable paths.

New Custom Rule Tasks resolve the binding in their creation transaction and freeze a secret-free
local or remote profile. Remote profiles include Space/connection UUID, provider, endpoint, image,
CPU, memory, timeout and configuration revision. Execution resolves the current token only if the
entire original identity still matches. Changing the Space default does not reroute existing Tasks.
ABI 1.6 retains historical 1.4 snapshots on their unchanged local path and 1.5 snapshots on their frozen local/remote path; old workers reject 1.6.
Tasks without snapshots remain explicitly legacy/local. New retests cannot bypass a current Space
sandbox requirement. No historical snapshot is rewritten.

## Enhanced Python runtime

New enhanced rules explicitly save `rule_runtime: "python-eval-v1"`, a `rule_runtime_manifest`
SHA256, and optional `materials`. Existing rules without these fields retain restricted semantics.
This runtime requires a qualified sandbox: local routing, an old plugin, a changed image or a changed
manifest fails closed before lease reservation. Source validation permits imports, helpers/classes
and either `evaluate(trace)` or `evaluate(trace, context)`; OS isolation is the security boundary.

The image-owned manifest contains `id`, `manifest_sha256`, `python_version` and exact `packages`.
The plugin advertises `python_eval_v1` and a manifest only for its configured immutable enhanced
image whose OCI `io.aivision.rule-runtime.manifest-sha256` label matches the shipped manifest.
AgentLens checks the frozen image and saved manifest hash again before admission. Read-only
`GET /api/v1/sandbox-connection/{uuid}/runtime?space_uuid=...` exposes connection UUID/revision/image,
general readiness, capabilities and matching manifests. Connection lists do not probe providers.
Django exposes it under the existing Space integration base and read permission.

Materials use `{files: [{path, encoding, content, sha256?}], expected_json?: string}`. New clients
send optional expected values as a UTF-8 JSON string. The server validates its JSON value but
preserves the exact string in normalization and Task snapshots, counting the original UTF-8 bytes
toward the material quota; the trusted context exposes its parsed value as `context.expected`.
Legacy `expected?: JSON` object/value input remains accepted. A request cannot contain both
`expected_json` and `expected`. The string form avoids cross-language number serialization changing
the measured or frozen bytes. File encoding is `utf-8`
or `base64`; hashes are verified/computed server-side. Limits are 20 files, 256 KiB decoded per file
and 512 KiB total, within the existing request envelope. Relative unique paths exclude traversal,
archives, symlinks, remote URLs and host mounts. ABI 1.6 freezes the normalized content/hash,
runtime manifest and existing image/resource profile; Trial and Runner send the same representation.

The context supports named reference reads and `run_python_tests(code, tests_path)`. Reference tests
are separate from generated code; bounded results include verdict, passed/failed/total and failure
cases. Candidate syntax/import/assertion faults are BAD; invalid tests, broken advertised libraries,
missing reports, no collected tests and incomplete skip/xfail verification are execution failures.
This is ordinary correctness testing, not tamper-proof adversarial grading. User output is captured
separately from the trusted JSON envelope; all children share the workload deadline and cleanup.
Successful execution evidence includes bounded `captured_stdout`, `captured_stderr`,
`output_truncated`, `rule_runtime` and `rule_runtime_manifest`. The host verifies the worker-returned
manifest before releasing success. Safe failure codes preserve `rule_test_harness_error`,
`rule_test_incomplete`, `rule_timeout`, `rule_output_too_large` and `rule_runtime_unavailable`; raw
worker failure reasons are not exposed by the service.

Enhanced execution uses `min(ENHANCED_EXEC_TIMEOUT, frozen connection timeout)`; the service default
is 30 seconds and ceiling is 300. Restricted rules keep `EXEC_TIMEOUT` unchanged. New recommended
connections use one CPU, 1 GiB and 30 seconds; existing connections retain their configured values.
A manage-authorized profile edit can apply these bounds on a compatible plugin, using the existing
lease/revision fences. The optional PATCH `expected_config_revision` compares the observed revision
under the same row lock; a stale profile returns 409 without any mutation. An older/manual plugin requires ownership-aware remove/reinstall; changing
only its budget cannot add this runtime and no automatic in-place upgrade is promised.

## Execution and teardown

Restricted Custom Rule keeps AST validation, one `evaluate(trace)` entry and the existing metrics-only result
contract. The trusted Runner/Trial binds the provider adapter; a selected backend without an adapter
fails structurally. Code and Trace data travel through bounded stdin JSON using the existing
`date`/`datetime` encoder. The operator fixes the executable argv and allowlists images. Rules cannot
choose packages, commands or policy files. The default native worker is `/app/src/agentlens-rule-worker`.

Restricted execution is bounded by `min(EXEC_TIMEOUT, frozen connection timeout)`. The default is five seconds;
connection timeout supports 1–300 seconds and cannot raise the global ceiling. Provisioning and
teardown use separate bounded budgets. The HTTP client budgets are create 120 seconds, execute
command timeout plus 55 seconds, and delete 45 seconds. The synchronous Django Trial proxy budget
is 600 seconds; deployments must allow this on their request/reverse-proxy path.

Every lease belongs to Space, Task, attempt, diagnoser and logical execution. Create is idempotent
by immutable lease ID/fingerprint. Conflicting reuse and duplicate execute are refused. Active,
uncertain and cleanup-pending leases all count against capacity. Rule data and tokens are absent
from the durable SQLite plugin journal. AgentLens keeps lease references independently of Task stats.

A command success is released only after whole-sandbox teardown and independent Docker readback
confirm that matching containers and volumes are gone. Each lease must first observe its immutable
sandbox on that daemon and retain the daemon identity; a different or never-observed daemon cannot
prove absence. Timeout, malformed output, cancellation
uncertainty and transport errors take the same teardown path. OpenShell 0.1.6 can return timeout 124
while parent/child processes keep running: the exit code alone never proves termination. Failed
verification returns `sandbox_cleanup_pending`, retains capacity and is retried by janitors. Plugin
startup reclaims nonclosed leases; AgentLens retries pending references every
`SANDBOX_CLEANUP_INTERVAL_SEC` (default 30 seconds), one bounded cleanup per pass with fresh DB
sessions. Closed state is monotonic. Task retention is not remote-resource cleanup.

A create-phase `ConnectError` or `ConnectTimeout` proves the HTTP request was not sent: AgentLens
closes only its local reservation and does not issue a delete for that nonexistent admission. A read
or write failure, lost response, or any failure after create remains uncertain and requires verified
remote cleanup. Expected configuration rejection isolates the affected scheduled Job; it does not
stop unrelated Jobs/Spaces in the same scan.

The first provider requires numeric nonroot UID/GID, Landlock `hard_requirement`, no network egress
and no policy middleware. Docker inspection uses GET-only requests to the operator-configured daemon.
The plugin is privileged infrastructure because its gateway and verification path control a Docker
host; use a dedicated trusted execution host and restrict plugin access to AgentLens.

## Plugin HTTP protocol v1

All routes below require a Bearer token. Public errors use fixed codes/messages and allowlisted
numeric input-size details. New AgentLens and plugin installations default to an 8 MiB
(8,388,608 byte) complete input budget, with a 16 MiB (16,777,216 byte) hard ceiling.
Code, the full Trace (including original `payload` and flattened attributes), materials, expected JSON
and protocol overhead share this budget. Both the UTF-8 request and the final normalized worker stdin
(including material hashes and the newline) must fit before AgentLens reserves any lease.

Set `SANDBOX_MAX_INPUT_BYTES` on AgentLens and `AGENTLENS_SANDBOX_PLUGIN_MAX_INPUT_BYTES` on the
plugin to an integer from 1,024 through 16,777,216. The effective allowance is the minimum of these
settings and the selected worker's qualified reader limit. A larger allowance requires an immutable
worker image with the image-owned `io.aivision.sandbox.max-input-bytes` label. Missing, mutable,
unreadable or invalid image metadata falls back to the legacy 2 MiB reader limit. An old plugin that
supports preflight but omits image limits also remains at 2 MiB; increasing only one setting does not
upgrade another component. Trace fields are never silently removed or truncated to fit.

The editor calls `/api/v1/diagnoser/rule-input-preview` through the Space-authorized platform proxy.
Preview and Trial share scope resolution, framework projection, material normalization and wire
construction. Preview creates no lease and executes no submitted code. It returns measured request
and worker bytes, the effective limit and selected transport, or a bounded failure code and size
details. The editor invalidates the result when code, materials, Trace, connection or identity changes
and enables Run only after the current check passes. Static validation diagnostics retain the
line and reason without echoing source snippets. Execution always repeats admission checks.
The 16 MiB reader bound belongs to the optional compiled sandbox entrypoint; the historical
local worker keeps its existing protocol behavior.
Worker output remains bounded to 1 MiB by default; the HTTP client also bounds responses. No redirects
or environment HTTP proxies are followed by AgentLens.

OpenShell 0.1.6 limits each decoded gRPC message to 1 MiB. Its Python `exec_stream` API streams
output only. The adapter measures the actual serialized protobuf: requests at or below 1 MiB use
`ExecSandbox`, and larger stdin uses `ExecSandboxInteractive` with a metadata-only start and 4 KiB
stdin frames. Clean request EOF closes stdin; success requires an exit event and successful final RPC
status. Timeout, cancellation and output-limit handling still require verified whole-sandbox teardown.
This transport change does not raise the total input allowance or upgrade the pinned provider.

AgentLens requires the side-effect-free preflight endpoint before create. Update the optional plugin
together with AgentLens: an old plugin without this endpoint fails with
`sandbox_plugin_upgrade_required` before either lease is reserved. Readiness alone does not establish
this capability. A bounded HTTP 413 `SANDBOX_INPUT_TOO_LARGE` maps to `sandbox_input_limit`; HTTP 422
`SANDBOX_RUNTIME_UNAVAILABLE` remains a runtime error. Unknown proxy/provider errors stay transport errors.
Trial and persisted Record execution evidence retain only `stage`, `actual_bytes` and `allowed_bytes`;
the UI translates the size failure and suggests reducing materials or choosing a smaller complete
Trace. Provider response text and submitted content are not reflected into errors or lease journals.

| Method/path | Contract |
|---|---|
| `GET /v1/health` | Protocol/provider/version, readiness, capabilities, active lease count, draining flag, plugin `max_input_bytes`, per-image `input_limits` and read-only `environment` prerequisite facts |
| `POST /v1/executions/validate` | Bounded `{create, execute}` envelope; exact worker-input preflight, no lease or workload; returns input bytes, effective limit and transport |
| `POST /v1/sandboxes` | Lease ownership plus image/resources; returns immutable sandbox ID and ready state |
| `POST /v1/sandboxes/{lease}/execute` | Code, Trace and command timeout; returns bounded stdout/stderr, exit code and timeout/truncation flags |
| `GET /v1/sandboxes/{lease}` | Safe state and ownership only |
| `DELETE /v1/sandboxes/{lease}` | Repeated teardown; closed only after verified cleanup; unknown identities cannot claim absence |
| `POST /v1/admin/drain` | Fences new creates before reporting active count; cleanup continues; process-lifetime admission fence |

Safe removal drains first, refuses any active/pending lease and preserves the journal. A restart clears
the admission fence but reclaims durable orphans before accepting work. See the plugin settings and
Dockerfile for operator-only gateway, token file, worker argv, policy, journal and socket settings.

## Validation boundaries

Unit tests use synthetic rules/Traces and explicit fixtures. Disposable seekdb tests verify actual
persistence/locking independently of plugin tests. A real Docker/OpenShell run verifies the provider
version and local platform only. UI screenshots with API fixtures do not establish a live production
backend, quality benchmark, cold image pull or full compiled release. Record those separately for
any delivery candidate. Migration `012` is additive; reverting application code does not remove leases
or prove resource cleanup. Drain and verify the optional provider before removal or rollback.

## Deployment-host environment preflight

The recommended installer checks the selected local Docker daemon before image acquisition or
private setup state. Pinned OpenShell 0.1.6 requires Docker Engine 28.0+ and active Landlock ABI 3+
(normally Linux 6.2+ with Landlock enabled). A successful syscall probe, rather than the kernel
version string, is authoritative. When the CLI and Docker daemon use different kernels, including
macOS Docker VMs, the CLI explicitly defers Landlock qualification to the installed plugin. Its
authenticated health endpoint actively probes the actual Linux kernel and checks that it matches
the configured daemon before connection registration. A failed or missing verification blocks the
automatic registration; service readiness remains separate from actual execution and cleanup.

Installation errors identify the failed node and return safe typed remedies for Docker access,
unsupported Docker/Landlock, deployment selection, image acquisition, startup and registration.
After an administrator repairs an unsupported host, generate and execute a new installation
command on the deployment host. Retrying a download or service start does not repair missing
kernel capabilities. This check neither requires nor qualifies the parent kernel of a nested VM.
