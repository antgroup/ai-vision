# AgentLens ADR Guidelines

An Architecture Decision Record (ADR) records a design decision that changes long-term system
constraints and explains its rationale. Code, tests, `docs/architecture.md`, and topic documentation
describe how the current system works; ADRs do not replace current behavior documentation.

This directory uses an **object / topic / decision record** structure:

```text
docs/adr/<object>/README.md
docs/adr/<object>/<topic>.md
```

- An object is a domain entity or subsystem with its own identity, lifecycle, or stable shared contract.
- Each file within an object directory covers one stable, atomic topic.
- Each level-two heading in a topic file starts one ADR.
- Append later decisions for the same object and topic to the existing file; do not create synonymous topics.

## Decision object registry

This table is authoritative for object keys, directories, and scope. Before adding a record, check
this registry and existing topics. When scopes overlap, first identify the owner of the long-term
contract. Do not create duplicate archives based on code directories, callers, or workflow stages.

<!-- adr-objects:start -->
| Object key | Object archive | Scope |
|---|---|---|
| `trace` | [Trace](trace/README.md) | Raw Trace facts received by the diagnostic runtime and expression-based reads |
| `projection` | [Projection](projection/README.md) | Semantic Trace projections, runtime facts, and Prompt variables |
| `dataset` | [Dataset](dataset/README.md) | Dataset definitions and diagnostic target selection |
| `trace-retrieval` | [Trace Retrieval](trace-retrieval/README.md) | Diagnostic candidate queries, full Trace retrieval, and detail replay |
| `diagnoser` | [Diagnoser](diagnoser/README.md) | Diagnoser types, modes, metric definitions, and runtime configuration sources |
| `diagnoser-rule` | [Rule Diagnoser](diagnoser-rule/README.md) | Input facts, evaluation rules, evidence, and execution boundaries of built-in Rule diagnosers |
| `executor` | [Executor](executor/README.md) | Execution plugin contracts, pre-evaluation, and result parsing |
| `llm-judge` | [LLM Judge](llm-judge/README.md) | LLM evaluation requests and reasoning modes |
| `job` | [Job](job/README.md) | Definitions of recurring or manual diagnostic jobs |
| `task` | [Task](task/README.md) | Lifecycle, attempt ownership, and retention of one task execution |
| `scheduler` | [Scheduler](scheduler/README.md) | Leader election, task dispatch, and failure recovery |
| `task-runner` | [Task Runner](task-runner/README.md) | Worker-side task execution orchestration and terminal-state finalization |
| `metric-result` | [Metric Result](metric-result/README.md) | Per-metric diagnostic facts, status, and evidence |
| `diagnosis-record` | [Diagnosis Record](diagnosis-record/README.md) | Trace-level diagnostic records, persistence policies, and queries |
| `task-stats` | [Task Stats](task-stats/README.md) | Task terminal-state statistics, metric counts, persistence, and queries |
| `scope` | [Scope](scope/README.md) | Resource identity and consistency of cross-resource references |
| `resource-sharing` | [Resource Sharing](resource-sharing/README.md) | Discovery and use of shared resources |
| `model-connection` | [Model Connection](model-connection/README.md) | Model connection resource contracts and credential handling |
| `sandbox-connection` | [Sandbox Connection](sandbox-connection/README.md) | Sandbox Connection resource and execution contracts |
| `sandbox-execution` | [Sandbox Execution](sandbox-execution/README.md) | Sandbox Execution resource and execution contracts |
| `security` | [Security](security/README.md) | Service authentication, authorization responsibilities, and sensitive-information boundaries |
| `http-api` | [HTTP API](http-api/README.md) | Shared HTTP contracts across resources; resource-specific APIs belong to their respective objects |
<!-- adr-objects:end -->

All built-in Rule diagnosers belong to `diagnoser-rule`, with one topic per diagnoser. Do not create
empty topics for built-in Rule diagnosers that have no ADR yet. Create an object for another concrete
Diagnoser only when its first long-term decision requires an ADR; do not create empty directories
for every registered diagnoser. `sample-selector` is not a separate object; its decisions belong to
`dataset`.

## Ownership and atomicity

A record may normatively change only one `object + topic`:

1. Choose the long-term domain contract being changed as the primary object, rather than the code
   directory, consumer, or current workflow stage.
2. Fields, algorithms, and policies within an object are topics of that parent object by default.
   Promote them to objects only when they acquire their own identity, lifecycle, or stable shared contract.
3. Revise the same record throughout a design review until acceptance. To change an accepted conclusion,
   append a complete new record to the same topic file.
4. A new record must state the topic's complete resulting decision and use `supersedes` to replace the
   previous record in full. Do not record only a delta, partially supersede a record, or create a synonymous topic.
5. Split coordinated changes into independently reviewable records for each object and connect them
   through `related_records`. List consumers that need adaptation in the primary record's `affected_objects`.

`supersedes` may refer to only one predecessor for the same object and topic. `affected_objects` points
from the decision's primary object to the affected objects; generated archives show this direction
as `A → B`. `related_records` does not imply impact, supersession, or chronological order; archives
show this undirected relationship as `A ↔ B`.

## When an ADR is required

Prepare an ADR and complete developer review before changing:

- Architecture, layers, cross-module data flows, or sources of facts.
- Data models, state machines, concurrency, scheduling, recovery, retention, or time semantics.
- HTTP, Trace, Projection, Executor, Prompt, or persistence contracts.
- Resource identity, sharing, authorization, security, or credential boundaries.
- Compatibility, migration, or rollback policies that constrain future implementation choices.
- Conclusions in existing ADRs, including amendments and replacements.

Bug fixes, additional tests, documentation corrections, and implementation improvements within an
accepted ADR normally need no new record when they preserve existing contracts. If implementation
requires changing an accepted conclusion, pause implementation and return to review.

## Status and timestamps

Maintain decision status separately from implementation status:

| Field | Value | Meaning |
|---|---|---|
| `decision_status` | `proposed` | Under review; not a target constraint |
| | `accepted` | Accepted; a target constraint that subsequent implementation must follow |
| | `rejected` | Not adopted; retained only as discussion context |
| | `superseded` | Replaced in full by a new record in the same topic |
| `implementation_status` | `not_started` | Implementation has not started |
| | `partial` | Only some code, tests, or current behavior documentation has landed |
| | `implemented` | The full decision scope has been implemented |
| | `retired` | The corresponding implementation is no longer part of the current system |

A topic may have at most one `proposed`, one `accepted`, and one `implemented` record. When a new target
has been accepted but is not fully implemented, the superseded record may temporarily remain
`implemented`. Set the old record to `retired` once the new implementation is fully in service.

Use RFC 3339 timestamps with minute precision and an explicit `+08:00` offset, for example
`2026-08-23T14:30+08:00`. Use `null` for unknown timestamps; do not infer them. Timestamps must satisfy
`proposed_at <= decided_at <= implemented_at` when present.

## Object archive format

Each object's `README.md` maintains its scope. The tool generates topic indexes and cross-object
references. Empty objects use the same structure, without invented topics or placeholder records.
The front matter `title` must exactly match the document's H1:

```markdown
---
object_key: trace
title: Trace Decision Archive
scope: Raw Trace facts received by the diagnostic runtime and expression-based reads
---

# Trace Decision Archive

## Current decision index

<!-- adr-index:start -->
<!-- adr-index:end -->

## Related cross-object decisions

<!-- adr-related:start -->
<!-- adr-related:end -->
```

## Topic and record format

A topic file has front matter containing the object key, topic key, and readable title. Its `title`
must exactly match the document's H1. Start a new proposal as `proposed` and `not_started`, with
`decided_at` and `implemented_at` set to `null`:

````markdown
---
object_key: trace
topic: runtime-input
title: Trace Runtime Input
---

# Trace Runtime Input

## trace-runtime-input@20260823-1430

**Unify the Trace runtime input boundary**

```yaml
record_id: trace/runtime-input@20260823-1430
decision_status: proposed
implementation_status: not_started
proposed_at: 2026-08-23T14:30+08:00
decided_at: null
implemented_at: null
supersedes: []
related_records: []
affected_objects: []
canonical_doc: docs/projection/trace-contract.md
```

### Background

...

### Decision

...

### Unchanged

...

### Impact and risks

...

### Alternatives

...

### Validation

...

### Implementation notes

...
````

The example's record ID, timestamp, and empty relationship lists are illustrative. Use the actual
proposal timestamp and corresponding ID, verify every referenced record and affected object, and
select the relevant current behavior document. Set acceptance and implementation metadata only when
those events have actually occurred; the example does not establish an accepted or implemented decision.

Record headings use `<object-key>-<topic-key>@<proposal-timestamp>`. Native Markdown anchors remove
`@`, so the example heading produces `#trace-runtime-input20260823-1430`; the tool generates record
links using this rule. `record_id` has the form `object-key/topic@proposal-timestamp` and is globally
unique. Record YAML does not repeat `topic` or `primary_object`; the file path and front matter define
them unambiguously. `related_records` is optional; all other fields in the example are required.

Every record must include **Background, Decision, Unchanged, Impact and risks, Validation, and
Implementation notes** in that order. **Alternatives** is optional. New records use the English
headings above; the validator also recognizes the corresponding legacy Chinese headings without
changing their meaning or order. Duplicate sections, including the same section in both languages,
are invalid. Do not add `legacy_id`, `baseline`, `record_kind`, or equivalent migration-classification fields.

Use `retired` only for a `superseded` record whose implementation has left the system; it cannot be
combined with `accepted`.

When establishing the initial current baseline, record only implemented behavior verified against
code, tests, and current documentation. Do not import unimplemented targets, obsolete proposals, or
mappings from old numeric ADRs into the new archives; use Git for historical investigation.

## Discovery and synchronization

Before designing or changing behavior:

1. Find the object in the registry and read its `README.md` topic index.
2. Read the target decisions, currently implemented decisions, and cross-object references for the relevant topics.
3. Follow `supersedes` and `related_records` to understand evolution and coordinated changes.
4. Read `canonical_doc` and compare it with code and tests to establish current behavior.

Useful searches:

```bash
rg -n "<keyword>" docs/adr
rg -n "record_id:|affected_objects:|related_records:" docs/adr
```

When a decision is accepted, update only the ADR; do not describe an unimplemented target as current
behavior. When implementation status changes, synchronize code, tests, and `canonical_doc`. ADR
references must link to a specific `record_id`, not just an object archive or an ambiguous "latest ADR."

## Tools

After editing ADRs, run:

```bash
.venv/bin/python scripts/adr.py index
.venv/bin/python scripts/adr.py check
```

`index` generates each object's topic index and cross-object references. `check` validates registered
directories and unexpected files, front matter, H1 headings, record structure, statuses and timestamps,
object ownership, supersession and related-record relationships, current behavior documents, native
anchors, and index freshness. The root CI selects these checks for ADR/AgentLens changes and full
tiers, and checks that index generation leaves no diff. Remote required checks and pre-commit
hooks are separate configuration.

## Handling conflicts

When ADRs, code, tests, architecture, or topic documentation conflict, do not silently choose one:

1. Identify the records, files, and behavior involved.
2. Determine whether documentation is stale, implementation is incomplete, or the decision has changed.
3. Obtain developer confirmation.
4. Synchronize the record statuses, implementation, and current behavior documentation.

`README.md` is the project overview and development entry point, not the authority for detailed runtime behavior.
