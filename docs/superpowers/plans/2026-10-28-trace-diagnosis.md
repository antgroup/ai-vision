# Trace Diagnosis Implementation Plan

> **For agentic workers:** Use the approved design below. Implement independently owned frontend and backend tasks, then request a fresh-context review of the integrated diff.

**Goal:** Run an existing diagnoser or configured specialist on selected traces without a separate Quick Check product or duplicate trace picker.

**Architecture:** The trace list/detail supplies 1–10 exact IDs. A single saved diagnoser supplies its rules, model connection and specialist configuration; AgentLens freezes that definition into a bounded task. Reuse request receipts, task polling, record history and authorization guards. Keep legacy Quick Run endpoints and frozen historical tasks compatible.

**Tech Stack:** Existing React, TypeScript, TanStack Query, Django and AgentLens.

**Spec:** User-approved convergence on 2026-10-28, captured below.

## Approved design and constraints

- The only trace action is **Run diagnosis** / **运行诊断** / **診断を実行**.
- Select one saved diagnoser, including configured specialist checks. Do not introduce scenario presets, an independent rule pack, or a model override here.
- Configuration stays on the existing diagnosers page. Provide a scoped link and refresh when no suitable diagnoser is ready.
- The list supplies at most 10 checked IDs; detail supplies exactly its current ID. The dialog never fetches candidates.
- Preserve immutable request retry, frozen definitions, honest partial/failed results, permission revocation and history links.
- Keep the large failed-projection snapshot fix and fixed-task historical record query fix.
- Preserve SigNoz-only diagnosis and user/Space/Agent boundaries; no controls on public or Langfuse data-source surfaces.
- Update Chinese, English and Japanese. No dependency, database migration or release-version changes.
- Private specialist policy remains in the authorized AgentLens task snapshot, never a Django receipt or browser response.

## Review focus

1. Missing, deleted, changed or cross-Agent diagnosers must fail safely; accepted retries must not create a new task.
2. Saved model configuration and private specialist policy must be frozen and bounded without changing legacy Core behavior.
3. Trace selections and late responses must not cross identity, Space, Agent, source or filter changes.
4. Projection failures on large traces must persist compact failure records; fixed-task queries must recover old records without widening ordinary queries.
5. The real UI must use the saved-definition API, show localized unavailable states, and never call the duplicate candidate scan.

## Task 1: Saved-diagnoser execution contract

**Files:** Django diagnosis input/run services, views and URLs; AgentLens quick-run service/routes, execution policy/snapshot and runner; their targeted unit tests and ADR/API contracts.

**Interfaces:** `GET /diagnose/runs/options`, `POST /diagnose/runs` with `{request_id, trace_ids, diagnoser_uuid}`; accepted/provisioning receipt remains unchanged. Safe options expose diagnoser identity, name, engine, kind and availability. One saved diagnoser per run; at most 10 traces and the existing 30 total / 3 per-case model-request limits.

- [x] Add failing tests for saved rule/specialist execution, model/identity rejection, immutable retries and private policy boundaries.
- [x] Implement the saved-definition branch and `bounded_diagnosis_v1`, preserving the legacy endpoint and policy.
- [x] Run relevant AgentLens/Django suites and update accepted API/ADR contracts.

## Task 2: Unified trace entry

**Files:** Trace diagnosis dialog/panel/API, request intent/types, native trace list/detail, quality dashboard, three locales and frontend regression tests.

- [x] Add failing tests proving the dialog submits an exact saved UUID, has no scenario/model picker, blocks unavailable definitions and keeps immutable retry/identity behavior.
- [x] Replace the Quick Check UI with saved-diagnoser selection and a scoped configuration link; rename the trace action/components.
- [x] Preserve selection, source navigation, result polling, history and revocation behavior; test list/detail and fixed-task history.
- [x] Run focused tests, locale checks, changed-file lint, typecheck and build.

## Task 3: Real regression and delivery

**Files:** `ai-vision/docs/diagnose/validation/` report, safe receipt and real screenshots.

- [x] Restart only this task's services after backend changes; exercise saved rules and configured specialist checks through real APIs and UI, including negative authorization/configuration cases.
- [x] Recheck large-trace compact failure export and historical fixed-task queries against the final code; distinguish synthetic inputs, real storage/services and untested external models.
- [x] Inspect three locales, both themes and narrow layout; verify no duplicate candidate scan and capture actual screenshots.
- [x] Run a fresh-context whole-diff review, fix findings and rerun affected checks.
- [x] Inspect final diff/staged files for unrelated changes and secrets, and refresh the target branch.

**Delivery:** Commit the reviewed tree, push the branch and create/attach a PR with the actual evidence and limitations.

## Decisions

- A single saved diagnoser per invocation keeps model budgets predictable. A specialist can contain its own configured checks; users may run another diagnoser afterward.
- Retain internal `quick_run` metadata and legacy routes where required for stored task/receipt compatibility. Product-facing copy uses diagnosis terminology.
- The previous Quick Check UI evidence is superseded by the new run. Earlier bug reproduction receipts remain valid only for their recorded source versions.
