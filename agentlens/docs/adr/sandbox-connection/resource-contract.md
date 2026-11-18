---
object_key: sandbox-connection
topic: resource-contract
title: Sandbox Connection contract
---

# Sandbox Connection contract

## sandbox-connection-resource-contract@20261102-1700

**Sandbox Connection contract**

```yaml
record_id: sandbox-connection/resource-contract@20261102-1700
decision_status: superseded
implementation_status: retired
proposed_at: 2026-11-02T17:00+08:00
decided_at: 2026-11-02T17:00+08:00
implemented_at: 2026-11-02T17:50+08:00
supersedes: []
related_records: []
affected_objects:
  - diagnoser
  - task
  - security
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

Applications need optional sandbox connections without changing the base deployment or overloading telemetry Agent identity.

### Decision

- A connection is a Space-owned resource with an immutable UUID, provider, authenticated plugin HTTP endpoint, profile and revision. It is distinct from Agent ingestion installations and model connections.
- Profiles include image, CPU, memory and execution timeout. The plugin's gateway identity, executable, policy and allowed images are operator-controlled. Core services do not install arbitrary packages or hold Docker access.
- Plugin credentials are write-only in API DTOs and excluded from snapshots, logs and provider error messages. Credentials may rotate for the same frozen endpoint/profile. Endpoint changes require new credentials and invalidate readiness; profile changes increment revision.
- A Space binding selects an optional default connection and may require sandbox use. Diagnosers choose inherit, required or local; required Space policy forbids local. Absence of a configured provider preserves existing restricted local execution. Selection does not depend on health and never silently falls back.
- Active bindings, pending Tasks and unverified leases block connection deletion; profile identity cannot change while cleanup is outstanding. Installation, verification and binding are separate observable states.
- Django enforces dedicated read/manage permissions and exact Space ownership before proxying. AgentLens independently checks resource references and exposes no credentials. All configured connections are explicit administrator/developer choices.

### Unchanged

Existing diagnostic algorithms, Space authorization, record identity and Task attempt/CAS rules remain authoritative. Telemetry integrations and LLM Judge are unchanged.

### Impact and risks

This is optional source functionality, not a physical release authorization. Provider support is qualified independently by version and platform. An unavailable selected sandbox fails closed; unknown capabilities are not treated as guarantees.

### Validation

Exercise scoped resources, strict DTOs, credential exclusion, routing compatibility, bounded execution, timeout/error cleanup, restart recovery and independent driver readback. Label fixtures, actual services and unrun delivery/platform scope separately.

### Implementation notes

The user explicitly approved the optional sandbox architecture and requested implementation. This record codifies that accepted design. Source implementation and local verification are complete using synthetic inputs. Production native image assembly, full layer audits and additional platform qualification remain separate; no publication, commit or push is authorized by this record.

## sandbox-connection-resource-contract@20261103-1242

**Automate deployment-host enrollment while retaining explicit verified activation**

```yaml
record_id: sandbox-connection/resource-contract@20261103-1242
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-03T12:42+08:00
decided_at: 2026-11-03T12:42+08:00
implemented_at: 2026-11-03T13:25+08:00
supersedes:
  - sandbox-connection/resource-contract@20261102-1700
related_records: []
affected_objects:
  - executor
  - task
  - security
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

Requiring an ordinary user to supply plugin and workload images, a gateway, Docker
socket details, paths and credentials prevents self-service installation. The user
approved automatic installation on the existing AI Vision deployment host, safe
connection registration, and an explicit final action to enable the Space default.
This record documents that approval; it does not authorize artifact publication.

### Decision

- A connection remains a Space-owned resource with an immutable UUID, provider,
  authenticated plugin HTTP endpoint, resource profile and configuration revision.
  It is distinct from telemetry integrations and model connections.
- The default browser flow has no technical configuration fields. An explicit
  action prepares one host CLI command and a durable installation receipt. Django
  and AgentLens never receive a Docker socket or execute host installation commands.
- The CLI proves the target backend identity and local daemon, discovers the actual
  diagnosis route and private network, selects immutable platform-specific artifact
  references, and owns a dedicated gateway, private credentials, fixed no-egress
  policy and durable journal. It refuses unknown or mismatching resources. Existing
  gateways and explicit manual installation remain an advanced path.
- Bootstrap, progress reporting and connection enrollment have independent short-lived
  credentials bound to one Space, creator, immutable plan and claimed run. Every
  request rechecks authorization and revocation. Progress accepts only bounded typed
  events, never plugin keys, raw logs or arbitrary errors.
- The CLI generates the plugin key in a private host file. Enrollment sends it only
  through the dedicated authenticated endpoint. Django forwards it in memory to
  AgentLens and stores only hashes and safe registration metadata. API reads,
  browser storage, task snapshots and logs never return the key. AgentLens's existing
  credential storage behavior is unchanged; this decision does not claim encryption
  at rest.
- AgentLens enrolls an installation-derived deterministic UUID and fixed profile
  idempotently. A retry must match the same Space, profile and key; it cannot adopt
  a name collision, overwrite an edited connection or rotate credentials implicitly.
  Registration succeeds only after AgentLens verifies authenticated provider
  readiness from its own network route. This does not execute a workload.
- Installation, provider readiness, saved connection and Space binding remain separate
  facts. A normal browser session with CSRF and manage permission explicitly enables
  the saved connection. A fresh probe and binding-to-connection lock order commit the
  binding only if the verified revision, profile and credential still match. Preserve
  the existing `require_sandbox` value; do not silently require sandbox execution.
- Existing manual binding selection remains independent of health. `inherit`,
  `required` and `local` retain their routing behavior. Missing, disabled or unavailable
  selected providers fail closed; absence of a selected provider preserves local
  execution unless Space policy requires a sandbox.
- Active bindings, pending Tasks and unverified leases still block deletion or
  incompatible profile changes. New Tasks retain frozen, secret-free execution
  identity and resolve the current credential only for the matching profile.

### Unchanged

The execution protocol, fixed no-egress workload boundary, task snapshots, lease
cleanup fences, optional local execution, LLM Judge, and existing manual APIs
retain their current behavior. Gateway lifetime remains separate from plugin removal.

### Alternatives

A technical form, even with collapsed advanced fields, leaves users responsible
for deployment knowledge. An administrator's existing-service preset is useful
but does not install a new gateway. Giving the web backend Docker access adds a
host-control boundary that is unnecessary for a single explicit host command.

### Impact and risks

The optional image stays outside the default five-service bundle. Its immutable
catalog travels with release metadata; missing or unsupported artifacts are an
explicit prerequisite failure. Source-selfhost packaging and private artifact-only
delivery remain separate. Upstream gateway configuration, same-daemon cleanup,
supervisor callbacks and actual image-platform compatibility require real acceptance.
Revoking reporting or closing the page does not stop the host process. Gateway
retention is separate from the existing drain-and-remove plugin command.

### Validation

Verify authorization, purpose separation, response-loss retry, secret exclusion,
target/resource ownership and configuration drift. Exercise real row locks for
enrollment versus revocation and credential/profile changes during activation.
Run the actual default command against Django, AgentLens, a real database and
Docker, then verify a Rule Trial and cleanup. Capture the rendered no-input flow,
real progress, saved connection, explicit binding and refresh recovery. Fixture,
prepared-cache, source-image and unrun platform scopes must remain explicit.

### Implementation notes

The user explicitly requested this implementation and full real-flow screenshots.
Source APIs, concurrency tests and actual macOS ARM64/OrbStack acceptance are implemented.
The default command created its own gateway, private files and connection; the
explicit default action and a real inherited-sandbox Rule Trial passed, with closed
verified leases and independent same-daemon absence checks. Prepared upstream
image caches and a local source QA registry were used. Published artifacts,
private binary delivery and Linux AMD64 qualification remain separate. The
release artifact smoke now requires this installation, trial and cleanup path
when the manifest includes the optional catalog.
