---
object_key: sandbox-execution
topic: input-budget
title: Complete Sandbox Input Budget
---

# Complete Sandbox Input Budget

## sandbox-execution-input-budget@20261104-1710

**Qualify and preview the complete execution input without changing Trace fields**

```yaml
record_id: sandbox-execution/input-budget@20261104-1710
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-04T17:10+08:00
decided_at: 2026-11-04T17:10+08:00
implemented_at: 2026-11-04T17:59+08:00
supersedes: []
related_records:
  - sandbox-execution/lifecycle@20261103-1505
affected_objects:
  - executor
  - sandbox-connection
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

PR #143's independent review confirmed that streaming resolves the OpenShell 0.1.6
single-message limit. Full SQL hydration still expands a complete business Trace
above the separate 2 MiB total budget. Removing `payload` would break existing rules.

### Decision

- Preserve every source span and field, including both stored `payload` and logical
  attributes. Keep the existing JSON and projection contracts; introduce no deduplication,
  compression, artifact storage or host-execution fallback.
- New client/plugin defaults are 8 MiB, with a 16 MiB hard ceiling and lower operator
  limits. Code, complete Trace, normalized materials and protocol overhead share the
  budget. Both the serialized request and final worker stdin must fit.
- Qualify the reader bound against the selected immutable image's explicit capability.
  An image without the new capability retains the conservative 2 MiB limit. Matching
  dependency manifests alone does not establish a larger reader bound. The effective
  budget is the minimum of client, plugin and qualified reader limits.
- Expose a scoped, side-effect-free preflight using the same projection, material
  normalization and serialization as execution. Return byte counts and limits, never
  private input or credentials. Neither successful nor rejected preflight reserves a lease.
- Display the effective limit and measured input before Trial execution. Invalidate stale
  measurements when inputs or connection identity change. The server independently
  validates every actual execution; a browser measurement is not authorization.
- Retain the existing small streaming frames, bounded execution/output and independently
  verified cleanup. Formal Tasks use the same admission calculation as Trials.

### Unchanged

Trace storage/field access, dependency manifest identity, task snapshots, Space ownership,
material quotas, timeout/memory limits and the optional-provider boundary remain unchanged.

### Impact and risks

Larger inputs increase memory consumption in the control plane and workload. Finite input,
resource and concurrency bounds remain required. Old components keep their smaller effective
limit; rollback rejects newly oversized work rather than truncating inputs or changing routing.

### Alternatives

A versioned lossless deduplication contract could reduce repeated SQL representations, but
requires independent compatibility review. Removing fields or truncating Trace bodies is
incompatible. Artifact-backed inputs add ownership, hash, quota and cleanup responsibilities.

### Validation

Exercise actual ingestion, full SQL hydration, projection, Trial and persisted diagnosis records.
Check complete payload/attribute hashes, source completeness and per-record execution status.
Cover UTF-8, normalized materials, effective/hard bounds, older workers, zero-lease rejection,
compiled worker images and browser interactions. Distinguish synthetic controls from business
samples and qualify only platforms actually run.

### Implementation notes

The user explicitly approved the recommended 8 MiB default / 16 MiB ceiling plan and preflight
on 2026-11-04. Prepared Linux ARM64 qualification covers complete 7-span SQL hydration and framework projection,
Trial and persisted Runner records, exact 8 MiB admission, 16 MiB compiled-reader bounds and
no-lease rejection. No release or other platform qualification is implied.
