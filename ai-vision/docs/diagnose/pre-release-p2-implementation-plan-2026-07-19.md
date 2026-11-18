# Agent Diagnosis Pre-release P2 Implementation Plan

Date: 2026-07-19

Status: Implemented; automated acceptance and final review passed on 2026-07-20

Repositories:

- `agentlens/`
- `ai-vision/`

## Goal

P2 removes generated-code residue and compatibility paths after P0/P1 established
the canonical execution, Projection, MetricService, and DiagnosisRecord contracts.
It must reduce surface area without adding database fields, speculative domain
rules, or compatibility aliases.

## Implementation Status

All five commits are implemented. Final automated verification on 2026-07-20:

- AgentLens: 539 diagnosis/domain/service/kernel/web/infra unit tests passed.
- AgentLens: 4 Docker-backed integration tests passed.
- AI Vision: frontend typecheck passed.
- AI Vision: Django system check completed with only the pre-existing missing
  static directory warning; Dataset/security regression tests passed (17 tests).
- Browser QA was attempted against the existing localhost:5174 target, but the
  browser runtime reported no available browser. No second Vite server was started.

The proxy module was not split: it already centralizes forwarding and scope policy
through the existing _forward_* and scope helpers, and another layer would be a
shallow pass-through module.

## Guardrails

- No relational migration.
- No change to Task/Job/Dataset ownership or the deferred Task-scoped model design.
- Preserve `Diagnoser.mode` as the Rule/LLM classifier and preserve single- versus
  multi-metric execution protocols.
- Keep stable diagnosis semantics typed; keep raw input/output/state/evidence open.
- Do not modify `vfe/src/components/ui/`.
- Delete unused code instead of retaining wrappers or aliases.

## Commit 1: `refactor(diagnose-ui): remove stale DTO compatibility`

Status: implemented.

Repositories: AI Vision

Changes:

- Remove `gmt_created` from diagnosis DTOs and conversions; consume the current
  `gmt_create` contract only.
- Remove the remaining frontend `offline` Dataset union member.
- Remove `Diagnoser.jobUuid`; a Diagnoser has no single Job binding or run state.
- Remove the unused paused translation and stale audit/example wording. Job status
  remains `enabled | disabled`.
- Make scenario an open string with no `common` fallback. Missing scenario remains
  absent/empty rather than inventing a business category.
- Fix `extractMetrics()` to read metric definition objects as `{key,...}` and never
  cast them to `string[]`.

Acceptance:

- Typecheck passes with no `gmt_created`, `offline`, Diagnoser `jobUuid`, or paused
  diagnosis branches.
- Custom scenario values and single/multi metric definitions render correctly.

## Commit 2: `refactor(records): store one rendered prompt per agent run`

Status: implemented.

Repositories: AgentLens and AI Vision

Changes:

- Add `rendered_prompt` to the existing open `agent_run` evidence object produced by
  LLM executors.
- Remove per-metric `evidence.rendered_prompt` copies from `llm_single`, `llm_multi`,
  and `llm_custom`.
- The detail adapter reads only `agent_run.rendered_prompt`; remove
  `legacyRenderedPrompt` and per-metric Prompt fallback logic.
- Do not persist another top-level DiagnosisRecord field and do not add a table
  migration.

Acceptance:

- One bad multi-metric record contains exactly one rendered Prompt.
- Good/skipped/failed behavior and MetricResult evidence remain unchanged apart
  from duplicate Prompt removal.
- Drawer displays the same Prompt through the canonical agent-run location.

## Commit 3: `refactor(diagnose-ui): delete unused legacy surfaces`

Status: implemented.

Repository: AI Vision

Changes:

- Delete unreferenced `CreateJobModal.tsx`, `EditJobModal.tsx`, and the orphaned
  `JobListItem.tsx` cluster (`JobDetailDialog.tsx`, `ConfirmDeleteDialog.tsx`) after
  a final import search.
- Delete any remaining unused Dataset Modal/Dialog/form helper files.
- Keep active `JobDrawer`, `TaskDetailDialog`, Diagnoser modals, and model
  configuration UI.
- Remove obsolete proxy comments that name deleted UI components.

Acceptance:

- Repository search finds no imports or routes for deleted surfaces.
- Job create/edit/delete through the active drawer still typechecks and works in
  browser QA.

## Commit 4: `refactor(graph-ui): normalize Graph presentation`

Status: implemented.

Repository: AI Vision

Changes:

- Use consistent Graph component names and user-facing labels.
- Update the related imports, filenames, props, and comments.
- Preserve original source evidence during presentation changes.

Acceptance:

- Graph component names and labels are consistent.
- Graph replay and topology interactions remain unchanged.

## Commit 5: `refactor(diagnose): deepen oversized stable modules`

Status: implemented.

Repositories: AgentLens and AI Vision

Changes:

- Split `diagnoseApi.ts` into co-located contracts/adapters for records, quality,
  jobs, diagnosers, and Projection while preserving one domain-facing import seam.
- Split `TraceTranscriptDrawer.tsx` by responsibility: data orchestration, replay
  views, feedback form, and detail sections. Keep one drawer interface.
- Split `JobDrawer.tsx` into form state/payload mapping and visual sections without
  changing the active user workflow.
- Split large proxy code only where repeated forwarding policy becomes one deep
  helper; do not introduce a new generic proxy framework.

Acceptance:

- Public interfaces shrink or remain stable; callers do not learn new ordering or
  configuration rules.
- No module is created with only pass-through wrappers.
- Focused tests and typecheck pass after each split.

## Final Verification

AgentLens:

- Full diagnoser/domain/service focused suite.
- Docker-backed leader failover, retry, trigger, and record write integration tests.
- Ruff on all P2-touched Python files and `git diff --check`.

AI Vision:

- Django proxy/security tests and `manage.py check`.
- `npm run typecheck`.
- Browser QA against the existing `http://localhost:5174` server at desktop and
  narrow widths: Job/Task anchoring, overview trends, list pagination/filtering,
  lazy detail, feedback, and Graph replay.

## Explicit Non-goals

- Task-owned model configuration (deferred product/database decision).
- Recursive sensitive-content filtering.
- New Graph/Retrieval facts without representative trace evidence.
- Database migration or legacy-data backfill.
