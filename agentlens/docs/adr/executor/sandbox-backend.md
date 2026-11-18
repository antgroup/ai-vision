---
object_key: executor
topic: sandbox-backend
title: Custom Rule Sandbox Backend contract
---

# Custom Rule Sandbox Backend contract

## executor-sandbox-backend@20261102-1700

**Custom Rule Sandbox Backend contract**

```yaml
record_id: executor/sandbox-backend@20261102-1700
decision_status: superseded
implementation_status: retired
proposed_at: 2026-11-02T17:00+08:00
decided_at: 2026-11-02T17:00+08:00
implemented_at: 2026-11-02T17:50+08:00
supersedes: []
related_records: []
affected_objects:
  - diagnoser
  - task-runner
  - sandbox-execution
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

The existing Custom Rule subprocess boundary can be replaced by an optional remote isolation backend while preserving business semantics.

### Decision

- Custom Rule retains the rule_custom Executor, one AST-validated evaluate(trace) entry and the current metrics-only user result contract. Sandbox is an execution backend, not a new Diagnoser mode or user-selected executor class.
- Trusted Runner/Trial code binds the resolved frozen sandbox adapter to the Executor. A selected sandbox without an adapter fails structurally; no path falls through to the local pool or one-shot subprocess.
- Local rules retain EXEC_TIMEOUT and the existing worker pool. Remote execution is bounded by the lesser of the global execution ceiling and frozen connection timeout. Diagnoser spec cannot override that ceiling.
- Remote code/Trace transport preserves existing date/datetime JSON encoding. The host applies identical metric-name/verdict/result validation after remote teardown. Provider errors use safe ExecutorFailure codes, including cleanup_pending; a successful command is not a successful diagnosis until cleanup and result validation pass.
- Execution evidence records backend/provider/connection identity and timing without credentials, raw private provider failures or new user-controlled record fields. Normal LLM Judge and builtin rule algorithms do not acquire a sandbox.

### Unchanged

Existing diagnostic algorithms, Space authorization, record identity and Task attempt/CAS rules remain authoritative. Telemetry integrations and LLM Judge are unchanged.

### Impact and risks

This is optional source functionality, not a physical release authorization. Provider support is qualified independently by version and platform. An unavailable selected sandbox fails closed; unknown capabilities are not treated as guarantees.

### Validation

Exercise scoped resources, strict DTOs, credential exclusion, routing compatibility, bounded execution, timeout/error cleanup, restart recovery and independent driver readback. Label fixtures, actual services and unrun delivery/platform scope separately.

### Implementation notes

The user explicitly approved the optional sandbox architecture and requested implementation. This record codifies that accepted design. Source implementation and local verification are complete using synthetic inputs. Production native image assembly, full layer audits and additional platform qualification remain separate; no publication, commit or push is authorized by this record.


## executor-sandbox-backend@20261103-1505

**Enhanced Python runtime on the existing sandbox backend**

```yaml
record_id: executor/sandbox-backend@20261103-1505
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-03T15:05+08:00
decided_at: 2026-11-03T15:05+08:00
implemented_at: 2026-11-03T17:10+08:00
supersedes:
  - executor/sandbox-backend@20261102-1700
related_records:
  - sandbox-execution/lifecycle@20261103-1505
  - task/execution-snapshot@20261103-1505
affected_objects:
  - task
  - sandbox-execution
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

The existing Custom Rule subprocess boundary can be replaced by an optional remote isolation backend while preserving business semantics.

### Decision

- `rule_custom` additionally accepts explicit `rule_runtime: python-eval-v1`, a qualified runtime manifest SHA256 and normalized materials. Absence retains restricted AST validation and local/remote execution semantics. Unknown or incomplete runtime identity is rejected.
- Enhanced Python permits imports, helpers, classes and `evaluate(trace)` or `evaluate(trace, context)`. The image isolation boundary, rather than the legacy AST blacklist, contains this runtime. It requires a compatible sandbox and never falls back to local execution.
- The service uses `min(ENHANCED_EXEC_TIMEOUT, frozen connection timeout)` with a default of 30 seconds and a 300-second service ceiling; restricted rules retain the independent five-second default. Trial and formal Runner carry identical normalized runtime/material fields.
- The trusted context provides named text/binary reference materials and bounded Python reference-test execution. Candidate failures produce BAD; invalid reference tests, incomplete verification, missing advertised libraries and infrastructure failures produce execution FAILED. This is ordinary correctness testing, not adversarial tamper-proof grading.
- Existing metric validation and independently verified whole-workload cleanup remain required before success. Captured user output cannot corrupt the JSON protocol.


- Restricted Custom Rule retains the rule_custom Executor, one AST-validated evaluate(trace) entry and the current metrics-only user result contract. Sandbox is an execution backend, not a new Diagnoser mode or user-selected executor class.
- Trusted Runner/Trial code binds the resolved frozen sandbox adapter to the Executor. A selected sandbox without an adapter fails structurally; no path falls through to the local pool or one-shot subprocess.
- Restricted local rules retain EXEC_TIMEOUT and the existing worker pool. Remote execution is bounded by the lesser of the global execution ceiling and frozen connection timeout. Diagnoser spec cannot override that ceiling.
- Remote code/Trace transport preserves existing date/datetime JSON encoding. The host applies identical metric-name/verdict/result validation after remote teardown. Provider errors use safe ExecutorFailure codes, including cleanup_pending; a successful command is not a successful diagnosis until cleanup and result validation pass.
- Execution evidence records backend/provider/connection identity and timing without credentials, raw private provider failures or new user-controlled record fields. Normal LLM Judge and builtin rule algorithms do not acquire a sandbox.

### Unchanged

Existing diagnostic algorithms, Space authorization, record identity and Task attempt/CAS rules remain authoritative. Telemetry integrations and LLM Judge are unchanged.

### Impact and risks

This is optional source functionality, not a physical release authorization. Provider support is qualified independently by version and platform. An unavailable selected sandbox fails closed; unknown capabilities are not treated as guarantees.

### Validation

Exercise scoped resources, strict DTOs, credential exclusion, routing compatibility, bounded execution, timeout/error cleanup, restart recovery and independent driver readback. Label fixtures, actual services and unrun delivery/platform scope separately.

### Implementation notes

Source implementation and prepared-environment qualification are complete for the approved ordinary
correctness scope. Qualification used a local macOS ARM64 host and OrbStack Linux ARM64 workloads
with prepared dependencies and cached images; it is not cold-start or released-artifact evidence.
The corrected native worker passed actual reference tests after pytest callbacks were made
discoverable in the Cython-compiled runtime. Actual CPython and all six advertised libraries matched
the qualified immutable-image manifest.

The final real-provider acceptance passed 17 HTTP Trials and four persisted full-Runner Tasks:
Python reference tests, JSON/CSV/XLSX positive and negative cases, candidate errors, incomplete
verification, Unicode output, actual imports, a legacy restricted rule, bounded background-child
cleanup and timeout handling. The connection's temporary one-second timeout was restored to 30
seconds through revision CAS and readiness/effective-budget readback. Control-plane source checks
also cover authorization, raw expected-JSON byte preservation and configurable effective budgets.

The formal Tasks use explicit synthetic raw Trace reads and private Record DTO capture. They prove
the real snapshot/attempt/Runner/provider path, not telemetry ingestion or Record transport. Their
raw-span fixture retains unavailable semantic projection and incomplete-source statistics. SQL
readback confirms closed, cleanup-verified leases. Independent post-browser readback confirmed all
28 final-connection leases closed in SQL and the journal, the same Docker daemon with zero matching
workload containers/volumes, and the mTLS gateway with zero sandboxes/templates. The plugin remained
ready with zero active leases. Physical release, private native delivery and Linux/amd64
qualification remain separate and unqualified; this implementation status does not authorize
publication, merge, commit or push.
