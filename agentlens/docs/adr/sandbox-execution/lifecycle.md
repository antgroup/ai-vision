---
object_key: sandbox-execution
topic: lifecycle
title: Sandbox Execution contract
---

# Sandbox Execution contract

## sandbox-execution-lifecycle@20261102-1700

**Sandbox Execution contract**

```yaml
record_id: sandbox-execution/lifecycle@20261102-1700
decision_status: superseded
implementation_status: retired
proposed_at: 2026-11-02T17:00+08:00
decided_at: 2026-11-02T17:00+08:00
implemented_at: 2026-11-02T17:50+08:00
supersedes: []
related_records: []
affected_objects:
  - executor
  - task
  - task-runner
  - security
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

The actual OpenShell 0.1.6 Docker PoC returned timeout 124 while parent and child remained alive; resource teardown is therefore a mandatory platform contract.

### Decision

- The platform owns a versioned provider-neutral lifecycle. OpenShell is the first separately enabled plugin; no OpenShell or Deep Agents SDK is required by the base environment. A compiled plugin app may be present while provider dependencies are installed only in an optional image.
- Every lease belongs to one Space, Task, attempt, diagnoser and logical execution. Durable reservation/fingerprint makes create idempotent and conflicting or duplicate execution fail. Payloads and credentials are never saved in the lease journal.
- The plugin authenticates every operation, fixes the gateway/worker executable, enforces an allowed workload image and non-root deny-egress policy, and bounds request/output sizes, execution time and concurrent leases.
- Code and Trace data use the existing JSON worker protocol through stdin. AST validation and metric/result validation stay on the trusted host. The plugin cannot author diagnosis records or bypass source authorization.
- Timeout, transport uncertainty and output/protocol failures fence the lease. OpenShell 0.1.6 timeout exit 124 does not prove process exit; whole-sandbox deletion and confirmed completion are required. Immutable provider IDs prevent cleanup from deleting a replacement instance.
- Docker-provider confirmation includes read-only inspection of the operator-configured daemon for matching immutable sandbox containers/volumes. Missing inspection or cleanup failure remains cleanup_pending and retains capacity. The workload never receives this socket.
- Durable plugin restart recovery and a janitor retry orphan/pending cleanup. AgentLens reconciles its own pending references with fresh sessions and no transactions held across external I/O. Closed state is monotonic; Task retention does not substitute for remote cleanup.
- SDK/framework Backend implementations may be reused inside a provider but do not replace platform ownership, authorization, budgets or termination guarantees. No cloud, Kubernetes or MicroVM support is implied by this first Docker provider.

### Unchanged

Existing diagnostic algorithms, Space authorization, record identity and Task attempt/CAS rules remain authoritative. Telemetry integrations and LLM Judge are unchanged.

### Impact and risks

This is optional source functionality, not a physical release authorization. Provider support is qualified independently by version and platform. An unavailable selected sandbox fails closed; unknown capabilities are not treated as guarantees.

### Validation

Exercise scoped resources, strict DTOs, credential exclusion, routing compatibility, bounded execution, timeout/error cleanup, restart recovery and independent driver readback. Label fixtures, actual services and unrun delivery/platform scope separately.

### Implementation notes

The user explicitly approved the optional sandbox architecture and requested implementation. This record codifies that accepted design. Source implementation and local verification are complete using synthetic inputs. Production native image assembly, full layer audits and additional platform qualification remain separate; no publication, commit or push is authorized by this record.


## sandbox-execution-lifecycle@20261102-1833

**Clarify OpenShell workspace ownership and delete preconditions**

```yaml
record_id: sandbox-execution/lifecycle@20261102-1833
decision_status: superseded
implementation_status: not_started
proposed_at: 2026-11-02T18:33+08:00
decided_at: 2026-11-03T15:05+08:00
implemented_at: null
supersedes:
  - sandbox-execution/lifecycle@20261102-1700
related_records: []
affected_objects:
  - executor
  - task
  - task-runner
  - security
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

Review of the OpenShell 0.1.6 public protobuf and server implementation establishes that deletion is name-based without caller identity/version preconditions. Observed immutable-ID checks do not provide an atomic delete fence against external replacement. The lifecycle remains valid under an explicit exclusive-workspace operating boundary; this proposed replacement requests maintainer review of that limitation.

### Decision

- The platform owns a versioned provider-neutral lifecycle. OpenShell is the first separately enabled plugin; no OpenShell or Deep Agents SDK is required by the base environment. A compiled plugin app may be present while provider dependencies are installed only in an optional image.
- Every lease belongs to one Space, Task, attempt, diagnoser and logical execution. Durable reservation/fingerprint makes create idempotent and conflicting or duplicate execution fail. Payloads and credentials are never saved in the lease journal.
- The plugin authenticates every operation, fixes the gateway/worker executable, enforces an allowed workload image and non-root deny-egress policy, and bounds request/output sizes, execution time and concurrent leases.
- Code and Trace data use the existing JSON worker protocol through stdin. AST validation and metric/result validation stay on the trusted host. The plugin cannot author diagnosis records or bypass source authorization.
- Timeout, transport uncertainty and output/protocol failures fence the lease. OpenShell 0.1.6 timeout exit 124 does not prove process exit; whole-sandbox deletion and confirmed completion are required. Before-delete immutable-ID checks reject observed replacements. After-delete wait/readback verifies the original instance. OpenShell 0.1.6 cannot atomically fence an external administrator replacing the same name between those requests; do not claim otherwise.
- Docker-provider confirmation includes read-only inspection of the operator-configured daemon for matching immutable sandbox containers/volumes. Missing inspection or cleanup failure remains cleanup_pending and retains capacity. The workload never receives this socket.
- Durable plugin restart recovery and a janitor retry orphan/pending cleanup. AgentLens reconciles its own pending references with fresh sessions and no transactions held across external I/O. Closed state is monotonic; Task retention does not substitute for remote cleanup.
- OpenShell uses one durable plugin journal per exclusively operated workspace. Other SDK/CLI clients must not delete or recreate plugin-owned names. The default workspace is permitted only on a dedicated gateway. Gateway administrators are trusted operators; exclusivity is an operational prerequisite, not an SDK-enforced tenant boundary.
- A create connection failure proven to precede HTTP transmission can close its local reservation. Uncertain admission, write/read timeout and post-create failures remain fenced until remote teardown is verified. Cleanup polling recomputes every RPC/sleep allowance against one monotonic deadline.
- SDK/framework Backend implementations may be reused inside a provider but do not replace platform ownership, authorization, budgets or termination guarantees. No cloud, Kubernetes or MicroVM support is implied by this first Docker provider.

### Unchanged

Existing diagnostic algorithms, Space authorization, record identity and Task attempt/CAS rules remain authoritative. Telemetry integrations and LLM Judge are unchanged.

### Impact and risks

This is optional source functionality, not a physical release authorization. Provider support is qualified independently by version and platform. An unavailable selected sandbox fails closed; unknown capabilities are not treated as guarantees.

### Validation

Exercise scoped resources, strict DTOs, credential exclusion, routing compatibility, bounded execution, timeout/error cleanup, restart recovery and independent driver readback. Label fixtures, actual services and unrun delivery/platform scope separately.

### Implementation notes

This replacement is proposed, not accepted on a maintainer's behalf. Existing lifecycle code and local tests cover the stated implementation; operator exclusivity and the narrowed deletion guarantee require review in the PR. No production rollout is authorized.


## sandbox-execution-lifecycle@20261103-1505

**Qualify enhanced image identity before admission**

```yaml
record_id: sandbox-execution/lifecycle@20261103-1505
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-03T15:05+08:00
decided_at: 2026-11-03T15:05+08:00
implemented_at: 2026-11-03T17:10+08:00
supersedes:
  - sandbox-execution/lifecycle@20261102-1833
related_records:
  - executor/sandbox-backend@20261103-1505
  - task/execution-snapshot@20261103-1505
affected_objects:
  - executor
  - task
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

The actual OpenShell 0.1.6 Docker PoC returned timeout 124 while parent and child remained alive; resource teardown is therefore a mandatory platform contract.

### Decision

- Enhanced create and execute DTOs explicitly carry the versioned runtime ID and manifest SHA256; execute also carries normalized material contents and hashes. They participate in the durable admission fingerprint; callers cannot choose worker argv or install packages.
- Before reserving a lease AgentLens probes authenticated health outside all DB transactions and requires `python_eval_v1` plus a valid manifest matching the frozen exact image digest and saved manifest SHA256. The plugin independently qualifies its configured image against the image-owned manifest and OCI label before advertising it or admitting work.
- Materials have at most 20 unique relative paths, 256 KiB decoded per file and 512 KiB total, with UTF-8 or base64 content and optional JSON expected values. New clients carry expected values in `materials.expected_json`, a validated UTF-8 JSON string preserved and charged byte-for-byte; context.expected exposes its parsed value. Legacy `materials.expected` JSON values remain compatible, and both fields together are rejected. This fixes deterministic cross-language material accounting within the existing contract; the existing total transport bound still applies. No traversal, symlinks, archives, URLs or host-directory mounts are accepted.
- Enhanced children share one disposable workload and total command deadline. Generated files are separate from immutable references. Stdout/stderr and test reports are bounded; zero/skip/xfail-only or malformed verification cannot pass.
- Runtime discovery is a scoped read-only request; ordinary connection lists never probe providers. It returns only connection/revision/image, general readiness, qualified capabilities and manifest inventory. General readiness alone never establishes enhanced support.
- New canonical enrollment uses one CPU, 1 GiB and 30 seconds. Existing connections keep their configured bounds. Prototype/manual installs have no automatic in-place upgrade promise; compatible existing connections can explicitly apply recommended bounds using the existing fenced update path.


- The platform owns a versioned provider-neutral lifecycle. OpenShell is the first separately enabled plugin; no OpenShell or Deep Agents SDK is required by the base environment. A compiled plugin app may be present while provider dependencies are installed only in an optional image.
- Every lease belongs to one Space, Task, attempt, diagnoser and logical execution. Durable reservation/fingerprint makes create idempotent and conflicting or duplicate execution fail. Payloads and credentials are never saved in the lease journal.
- The plugin authenticates every operation, fixes the gateway/worker executable, enforces an allowed workload image and non-root deny-egress policy, and bounds request/output sizes, execution time and concurrent leases.
- Code and Trace data use the existing JSON worker protocol through stdin. Runtime-specific source validation and metric/result validation stay on the trusted host. The plugin cannot author diagnosis records or bypass source authorization.
- Timeout, transport uncertainty and output/protocol failures fence the lease. OpenShell 0.1.6 timeout exit 124 does not prove process exit; whole-sandbox deletion and confirmed completion are required. Immutable provider IDs prevent cleanup from deleting a replacement instance.
- OpenShell uses one durable plugin journal per exclusively operated workspace. Other SDK/CLI clients must not delete or recreate plugin-owned names; the default workspace requires a dedicated gateway. Gateway administrators remain trusted operators. Upstream OpenShell deletion is name-based without atomic expected-ID/version preconditions. Before-delete immutable-ID checks reject observed replacements and after-delete readback verifies the original instance; this is not an atomic fence against concurrent administrator replacement.
- Docker-provider confirmation includes read-only inspection of the operator-configured daemon for matching immutable sandbox containers/volumes. Missing inspection or cleanup failure remains cleanup_pending and retains capacity. The workload never receives this socket.
- Durable plugin restart recovery and a janitor retry orphan/pending cleanup. AgentLens reconciles its own pending references with fresh sessions and no transactions held across external I/O. Closed state is monotonic; Task retention does not substitute for remote cleanup.
- SDK/framework Backend implementations may be reused inside a provider but do not replace platform ownership, authorization, budgets or termination guarantees. No cloud, Kubernetes or MicroVM support is implied by this first Docker provider.

### Unchanged

Existing diagnostic algorithms, Space authorization, record identity and Task attempt/CAS rules remain authoritative. Telemetry integrations and LLM Judge are unchanged.

### Impact and risks

This is optional source functionality, not a physical release authorization. Provider support is qualified independently by version and platform. An unavailable selected sandbox fails closed; unknown capabilities are not treated as guarantees.

### Validation

Exercise scoped resources, strict DTOs, credential exclusion, routing compatibility, bounded execution, timeout/error cleanup, restart recovery and independent driver readback. Label fixtures, actual services and unrun delivery/platform scope separately.

### Implementation notes

Source implementation and prepared/cached local macOS ARM64 plus OrbStack Linux ARM64
qualification are complete. The corrected Cython-native worker passed real reference tests and
actual advertised-library imports. Final real-provider acceptance passed 17 HTTP Trials and four
persisted full-Runner Tasks, including timeout failure, a same-session background child absent
before a positive report, and legacy restricted execution on the enhanced image.

Actual OpenShell process mediation returns EPERM for process-group signaling and ENOSYS for
pidfd opening. The context therefore retains normal group cleanup first, then uses a Linux-EPERM
fallback bounded to two seconds and 4096 process entries, with bounded stat reads. It selects only
the current UID's owned subprocess session, excludes PID 1/current worker, and rechecks
UID/session/start-time identity immediately before positive-PID signaling. Unsupported identities,
other signaling errors or an unverified live session fail closed. When pidfds are unavailable this
check/signal sequence is not an atomic adversarial PID-identity fence. It is qualified only for the
approved non-adversarial correctness workload; it neither changes the isolation policy nor replaces
mandatory independently verified whole-workload teardown.

The formal Tasks use real persisted control rows and the production Runner, with explicit synthetic
raw Trace reads and private Record DTO capture. They retain unavailable semantic projection and
incomplete-source statistics; no telemetry ingestion, source SQL or Record transport is claimed.
Corresponding leases were read back in SQL as closed and cleanup_verified, and the temporary
timeout CAS was restored with readiness/effective-budget verification. Independent post-browser
readback confirmed all 28 final-connection leases closed in SQL and the journal, zero matching
workload containers/volumes on the same Docker daemon, zero sandboxes/templates through the mTLS
gateway, and a ready plugin with zero active leases. The exclusive-workspace boundary and absence of an atomic upstream
name-based deletion fence remain unchanged.

Qualification used a prepared environment and cached source-QA images. Cold start, physical
release, private native delivery and Linux/amd64 remain unqualified. No publication, merge, commit
or push is authorized by this implementation status.

The prior ownership clarification is incorporated into this accepted complete decision. Its historical body remains unchanged; its status now records supersession.
