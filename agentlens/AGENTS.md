# AgentLens Development Guidelines

This document defines engineering conventions for all AgentLens developers and coding agents. `AGENTS.md` is the single maintained source; `CLAUDE.md` must be a symlink to this file, with no separate content.

## Project Role

AgentLens is the AI Vision diagnostic backend. It manages and schedules diagnosers, datasets, and tasks; reads traces; runs diagnostics; and stores diagnostic records and terminal-state statistics. AI Vision calls AgentLens over HTTP and does not directly access the OceanBase instance, SigNoz telemetry database, or Antcollector used by AgentLens.

See `docs/architecture.md` for the current architecture and data flow.

## Authoritative Sources

Each source owns a specific set of facts:

| Information | Authoritative source |
|---|---|
| Engineering workflow, development constraints, and agent behavior | `AGENTS.md` |
| Current system boundaries, layers, and core data flows | `docs/architecture.md` |
| Design decisions and their evolution | `docs/adr/` |
| HTTP contracts | `docs/api.md` |
| Trace / Projection contracts | `docs/projection/trace-contract.md` |
| Configuration, logging, scheduling, and diagnosers | Corresponding topic documentation |
| Project introduction and quick development entry points | `README.md` |
| Current implementation | Code and tests |

The README is not authoritative for detailed runtime behavior. When ADRs, code, tests, architecture, or topic documentation conflict, do not silently choose one. Explain the conflict, determine whether documentation is stale, implementation is incomplete, or a decision has changed, and obtain developer confirmation.

## ADRs and Design Changes

ADRs use an object-directory / topic-file / decision-record structure. Each individual record within a topic file is an ADR. Before designing or changing behavior, consult the object registry in `docs/adr/README.md`. Based on the task domain, topic, and affected objects, read the target decisions, currently implemented decisions, and cross-object references in each object's `README.md`, together with the current behavior documentation linked from those records. Do not choose the "latest ADR" solely by date or file location.

Changes that require an ADR under `docs/adr/README.md` must complete design review and be ready for implementation first. Implementation must follow the relevant target decisions. If an accepted conclusion needs to change, pause implementation, append a complete new record under the same object and atomic topic, and request another review. Split coordinated changes across objects by each object's contract and link the records. After editing ADRs, run `.venv/bin/python scripts/adr.py index` and `.venv/bin/python scripts/adr.py check`. The root CI selects these checks for ADR/AgentLens changes and full tiers, and rejects stale generated indexes. This does not configure remote required checks or a pre-commit hook.

## Development Environment

- macOS and Linux are supported; Windows is not supported.
- Python 3.11 and 3.12 are supported.
- You may use `python3 -m venv .venv` to create the initial virtual environment.
- Once `.venv` exists, all local project Python commands must use `.venv/bin/*`, such as `.venv/bin/pip`, `.venv/bin/pytest`, and `.venv/bin/alembic`.
- Docker builds and commands inside containers use the container's Python and are not subject to the local `.venv` requirement.
- The primary local development setup is `.venv + Docker MySQL`; see `README.md`.

Common validation commands:

```bash
.venv/bin/ruff check src/agentlens tests
.venv/bin/python -m compileall -q src/agentlens
.venv/bin/pytest tests/unit/ -q
git diff --check
```

Format changed Python files with `.venv/bin/ruff format`, then verify those paths with `--check`. Add targeted tests according to the change. Cross-module or public-contract changes require broader relevant coverage. Explain any checks that could not run and the remaining risk. Historical migrations are excluded from the default Ruff paths; check modified migrations explicitly.

## Layer Boundaries

- `domain/` contains domain models, enums, and pure business structures, with no dependency on Web, Service, or Infra.
- `infra/` provides communication and persistence primitives for OceanBase, SigNoz, Antcollector, LLM Judge, and similar dependencies. It does not orchestrate business workflows.
- `service/` composes Infra clients to implement data-source, projection, storage, aggregation, and other business workflows.
- `diagnoser/` owns Executor registration, diagnostic definitions, and execution logic.
- `kernel/` owns heartbeats, leader election, scheduling, dispatch, rescue, and runners.
- `web/` owns routes, authentication, middleware, dependency injection, and response models.
- Shared utilities belong in `utils/`; do not use a utility directory to bypass clear domain ownership.

Reuse existing patterns and responsibility boundaries. New abstractions must address real complexity, duplication, or boundary problems. Avoid unrelated refactoring.

## Coding Conventions

- New OceanBase tables use the `agentlens_` prefix and singular names. Auto-incrementing `id` values are internal to the database; use business UUIDs across modules.
- When adding tables, update the ORM, migrations, and `obschema/ddl.sql` together.
- Python timestamps use system-local time through `datetime.now()`; do not independently introduce UTC semantics.
- Never include credentials, tokens, complete sensitive inputs, or unredacted evidence in logs or read responses.
- Client-side hiding, frontend parameters, and display text do not replace server-side security constraints.
- Before adding an Executor, runtime, Scope, API, or scheduling behavior, read the authoritative documents below. Do not duplicate their business rules here.

## Documentation by Task

Read the relevant documents before changing code and check whether they need updates afterward:

| Change area | Required reading and documentation to update |
|---|---|
| Architecture, layers, and core data flows | `docs/architecture.md`, relevant ADRs |
| Data models, Scope, and resource references | `docs/architecture.md`, `obschema/`, relevant ADRs |
| HTTP routes, parameters, and responses | `docs/api.md` |
| Configuration options or defaults | `docs/configuration.md` |
| Log events, fields, or routing | `docs/logging.md` |
| Diagnosers, Executors, registration, or prompt contracts | `docs/diagnoser/README.md` and relevant documents in `docs/diagnoser/` |
| Traces, Projections, Projectors, or variables | `docs/projection/README.md` and relevant documents in `docs/projection/` |
| Scheduling, leader election, Task states, or recovery | `docs/scheduling.md` |
| OceanBase connections and DDL | `docs/ob-connection.md`, `obschema/` |
| Images, Compose, or release startup | `docs/deployment.md` |
| Design-decision states or relationships | `docs/adr/README.md` and relevant ADRs |

Update the README only when the project role, development prerequisites, quick start, common development commands, deployment entry points, or user-visible capabilities change. Keep detailed implementation notes, counts, and change history in their dedicated documents.

Diagnoser designs, prompt contracts, and usage instructions belong in `docs/diagnoser/`, organized by capabilities such as `llm/`, `rule/`, and `executor/`. Retired implementations must not remain as current operating guides. Current architecture, API, and scheduling documentation owns any necessary migration guidance and historical-read boundaries. Never commit raw traces, real user samples, or private validation material; preserve the existing ignore rules.

Current Trace, Projection, and Projector behavior is documented in `docs/projection/`: shared contracts belong in `trace-contract.md`, and active Projector implementations each have a file directly in that directory. Projection ADRs remain organized by object under `docs/adr/projection/`.

## Change and Commit Review

After making changes:

1. Review `git status` and the complete diff. Confirm that the scope is explainable and contains no unrelated changes.
2. Check normal, error, concurrency, cancellation, and recovery paths, plus data, interface, and security compatibility.
3. Update relevant documentation and preserve table, code-block, and ASCII-diagram formatting.
4. Run tests, static checks, formatting checks, and `git diff --check` appropriate to the risk.
5. Remove dead code, duplicate logic, unused imports, debugging leftovers, and unnecessary compatibility code.

After the user explicitly requests a commit, review all pending changes again before `git add` or `git commit`, not just the last edited files. Fix failing checks first. If the environment or an external dependency blocks validation, explain the limitation and obtain the developer's acceptance of the remaining risk before committing.

## Agent Workflow

- Read code, tests, relevant ADRs, and topic documentation before choosing an implementation.
- Requests to inspect, review, analyze a cause, or discuss a design do not by themselves authorize edits.
- Before changing existing architecture, data models, interface contracts, scheduling semantics, query semantics, or security boundaries, explain the current behavior, proposed change, behavioral impact, compatibility and rollback risks, recommended approach, and alternatives. Obtain explicit confirmation from the current user.
- An explicit task to implement an accepted ADR authorizes implementation within that scope. Changes outside it require renewed confirmation.
- Do not revert or overwrite the user's existing changes. Understand relevant uncommitted work and work with it; do not restore files without authorization.
- Do not run `git commit` or `git push` unless the user explicitly requests it.
