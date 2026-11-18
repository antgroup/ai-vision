---
object_key: sandbox-execution
topic: input-transport
title: Bounded execution input transport
---

# Bounded execution input transport

## sandbox-execution-input-transport@20261104-1500

**Preflight complete input and stream across the provider message boundary**

```yaml
record_id: sandbox-execution/input-transport@20261104-1500
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-04T15:00+08:00
decided_at: 2026-11-04T15:00+08:00
implemented_at: 2026-11-04T15:00+08:00
supersedes: []
related_records:
  - sandbox-execution/lifecycle@20261103-1505
affected_objects:
  - executor
  - diagnosis-record
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

Issue #150 reproduced a transport failure for complete enhanced-rule input above the
OpenShell 0.1.6 gateway's 1 MiB decoder limit, despite the worker's 2 MiB budget.
The Python SDK's `exec_stream` method streams output but still sends stdin in one
message. The user approved revision and required actual verification with evidence.

### Decision

- Keep the complete HTTP execution envelope and final UTF-8 worker stdin bounded to
  2 MiB. Include source, every Trace field, normalized materials, expected values,
  JSON overhead and the trailing newline. A lower configured plugin limit wins.
  Do not truncate spans or waive the worker's independent bound.
- Before reserving any AgentLens or plugin lease, require authenticated
  `POST /v1/executions/validate`. The endpoint shares the real execution preparation
  and measures the exact provider request, without creating resources or journaling
  submitted contents. Execution repeats validation to enforce its own boundary.
- In the pinned 0.1.6 adapter, select unary execution by serialized protobuf size,
  not a guessed stdin allowance. Above 1 MiB, use `ExecSandboxInteractive` with a
  bounded metadata start, 4 KiB stdin frames and request EOF. Require both an exit
  event and successful final RPC status. Stop/cancel on bounded output overflow.
- Preserve timeout, cleanup, ownership, attempt and result-validation contracts.
  A transport success never substitutes for verified whole-workload teardown.
- Map only recognized bounded status/code pairs and allowlisted numeric size
  details. Unknown upstream errors remain generic; never echo raw provider text.
  Trial and Record expose the same stable code and safe size evidence.
- New AgentLens rejects a plugin without preflight before lease reservation and
  tells the operator to update it. This is an explicit paired-update requirement,
  not a provider-version bump or an automatic in-place upgrade promise.

### Unchanged

Provider version, worker image and manifest, authorization, complete Trace semantics,
and the existing whole-workload cleanup contract remain authoritative.

### Impact and risks

Paired AgentLens/plugin deployment is required. The adapter depends on the pinned
SDK's generated RPC definitions; tests exercise both unary and streaming messages.
Streaming solves the per-message limit without enlarging the worker input budget.

### Validation

Verify exact protobuf boundaries, complete UTF-8 hashes, final gRPC status,
cancellation, early zero-resource rejection, real provider execution and cleanup,
and Trial/Record presentation. Distinguish simulated gRPC service tests from real
OpenShell and qualify each actual platform separately. This source change does not
authorize a release, merge, or production deployment.

### Implementation notes

The approved issue revision is implemented in source. Local source-QA evidence uses
OpenShell 0.1.6 on macOS ARM64 with an OrbStack Linux ARM64 daemon; platform and
packaged-release qualification must be recorded independently.
