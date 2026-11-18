---
object_key: model-connection
topic: diagnostic-validation
title: Persisted Basic Diagnostic Validation
---

# Persisted Basic Diagnostic Validation

## model-connection-diagnostic-validation@20261021-0500

**Verify two bounded canonical text cases before choosing a diagnostic model**

```yaml
record_id: model-connection/diagnostic-validation@20261021-0500
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-21T05:00+08:00
decided_at: 2026-10-21T05:00+08:00
implemented_at: 2026-10-21T05:03+08:00
supersedes: []
related_records:
  - model-connection/resource-contract@20260823-0234
  - model-connection/connectivity-probe@20261021-0051
  - model-connection/credential-handling@20261021-0005
  - llm-judge/request-attempt-hooks@20261009-1945
affected_objects:
  - llm-judge
  - executor
  - security
canonical_doc: docs/api.md
```

### Background

A successful connectivity probe establishes that a provider accepts a request. It cannot establish
that the model returns the production diagnostic JSON and sourced citations, or discriminates an
obvious positive and negative case. Existing real-trace diagnoser Trial remains the broader check.

### Decision

- Add `POST /api/v1/model-connection/{uuid}/validate`, with the authorized `space_uuid` and optional
  strict `generation_options`. Return the full connection DTO with a nullable `diagnostic_validation`.
- The server owns two short synthetic `langchain` root spans. Both requests are exactly
  `Return exactly READY.`; positive final output is `READY`, negative final output is `BUSY`.
  Run the production projector, canonical `task_completion/text_response` definition, general Core
  executor, Judge JSON parser and strict request/final citation validator. Do not create Tasks,
  rewrite formal diagnoser specs, substitute a simplified prompt, or weaken evidence validation.
- The explicit validation profile defaults to `max_tokens=4096`, `enable_thinking=null`,
  `temperature=0`, `reasoning_effort=null`, `request_timeout_sec=45`. Token limits are 256–8192;
  phase timeouts are 10–60 seconds. Other supported generation controls follow the formal options.
  Record all effective controls and the SHA256 canonical-definition digest. The official Anthropic
  compatibility path records omitted temperature/thinking/reasoning controls as null.
- Each case may issue one real request: no retry and no separate ping, at most two requests total.
  Missing required evidence is resolved offline. Stop after an incomplete case. Check elapsed time
  before each case against twice the configured phase timeout; synchronous HTTP phase timeouts
  and this dispatch deadline are not an absolute wall-clock cancellation guarantee. Do not launch
  background requests that continue after returning a timeout. Stream at most 256 KiB per response.
- A non-error HTTP 2xx response can set connection availability even when diagnostic JSON,
  citations or verdicts fail. Transport/provider failures, truncation, absent evidence and exhausted
  budgets produce `incomplete`, not a claim of model incapability. Wrong verdicts or invalid
  diagnostic output produce `failed`; both expected verdicts and valid citations produce `passed`.
- Only finite safe status/reason fields, expected/actual verdicts, a timezone-qualified local timestamp,
  profile identity and token usage are persisted or returned. No raw exceptions, bodies, reasons,
  evidence, credentials or headers. Attempt observations preserve available usage even when a later
  citation validator fails. Missing usage remains null; totals are not inferred from other fields.
- Migration 011 adds nullable JSON `diagnostic_validation` and internal `config_revision` default 1;
  existing connections retain their credentials and status. Endpoint/model/key updates clear the
  report, reset connectivity status and atomically increment the revision. Snapshot reads end their
  transaction before network I/O. One SQL compare-and-swap checks Space, UUID, revision and connection
  identity before writing the report/status. An intervening edit, including an ABA edit, yields 409.
- Validate the finite report schema on writes and reads. GET/list hide malformed or stale-definition
  reports. A passed report applies only to this explicit basic text profile; changing diagnostician
  parameters or selecting another Core/Specialist does not inherit a “current configuration verified”
  guarantee. Existing `/test` and formal/Trial execution remain compatible.

### Unchanged

Existing connectivity probes, real-trace trials, formal definitions and Task scheduling retain their
contracts. A validation report does not certify other diagnostic settings or model quality.

### Impact and risks

The change uses the existing internal service-token boundary; the platform must enforce model
management permission and inject the authorized Space. Arbitrary trace uploads, prompts and
provider headers are not accepted by this endpoint. Custom saved endpoints retain their existing
outbound-network semantics. Reusing the lightweight probe would not test production diagnosis.
A browser-only result would be lost on reload and could not guard concurrent configuration edits.

Deploy migration 011 before the new application. Old application code can ignore the additive
columns. Downgrading the migration removes only validation reports/revisions; it does not alter
credentials or connection identities. Do not run new code against the downgraded schema.

### Validation

Offline tests use mocked HTTP transport while executing the real projector/Core/Judge/citation
chain. They cover schema/scope, positive and negative verdicts, missing citations/evidence,
provider and network errors, token accounting, oversized/deep JSON, stale reports, SQL CAS/ABA,
secret exclusion and legacy migration preservation. Independent seekdb tests cover persistence
and CAS across separate sessions. Real-provider and browser acceptance are separate checks
owned by the parent integration task; this record does not claim those runs.

### Implementation notes

Implemented the additive route, strict read/write schema, configuration revision and schema migration.
Focused offline and isolated seekdb persistence/CAS tests pass. All AgentLens writers must be upgraded
before validation is enabled; old writers do not clear reports or increment configuration revisions.
