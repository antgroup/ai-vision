# Observability views: pre-merge re-review, 2026-10-27

A second independent review covered PR #125 from main `historical-source-revision-redacted` to submitted head `historical-source-revision-redacted`. Backend/provider contracts, dashboard/metric state, and Trace rendering had separate review seats; the coordinator also reviewed MCP/Skill topic pages and imports. Two additional P2 defects were reproduced and repaired before merge. No further demonstrated required fix remained after an independent review of the repairs.

## Repairs and behavior

- **Latency chart selection followed an array position.** After isolating model A, a same-query refresh returning `[B, A]` displayed B. Measurement-series keys now encode the complete model identity with collision-free Unicode code-point boundaries. A real React/ChartPanel/helper regression clicks A, refreshes in reverse order, and asserts that A remains selected, including names containing chart-path characters. Statistical values, aggregation and chart modes are unchanged.
- **Unsupported sources claimed complete empty history.** The real Langfuse provider/service path performed no scan but returned successful MCP/Skill histories with `complete:true`. Providers now identify the unsupported capability; both Labs collectors return an explicit 3011 error. Original datasource routing and access checks remain in place. Overview and history tests use the actual view/service/Langfuse provider and assert zero outbound GET/POST calls. Genuine supported empty windows still return successful empty results, with `complete:true` for history.

Both regressions were observed failing before their fixes and passing after them. A separate reviewer inspected the eight-file repair delta and found no required follow-up.

## Final verification

Tested product commit: `historical-source-revision-redacted`. All 140 changed product/test files match the frozen source hashes taken for the final complete run. Subsequent evidence-only changes do not modify product sources. The [portable receipt](evidence/observability-merge-review-20261027.json) records these hashes, commands, counts and local-log hashes.

| Check | Result |
| --- | --- |
| Modified frontend ESLint: 124 TS/TSX files, zero warnings | Passed |
| `npm run typecheck`, followed by `npm run build` | Passed |
| Complete frontend `npm test` | 1,225 passed; zero failures/skips/cancellations |
| Actual Django test runner: 17 affected/compatibility modules | 311 passed |
| `git diff --check` and staged diff check | Passed |

Frontend checks ran in `ai-vision/vfe`, using Node 26.10.0 and existing cached dependencies. Backend checks used Python 3.12.14 / Django 6.0.3, in-memory SQLite and locmem cache. The final backend run explicitly permitted the MCP protocol test's temporary loopback HTTP listener; an earlier sandboxed attempt could not bind that listener and is not counted as a passing run. Provider dependencies remain mocked/local fixtures. The suite expands the earlier 15 modules with native Langfuse API and public Langfuse routing regressions. Existing runtime/staticfiles/SQLite and bundle-size warnings remain.

## Evidence and boundaries

The [original review and five screenshot pairs](observability-view-review-20261027.md) retain their original source binding to product `historical-source-revision-redacted`. They are not relabelled as new captures of the repaired head. The repairs change refresh selection and unsupported-source error behavior, covered by fresh DOM/API regressions; no additional pixel/browser acceptance is claimed for this re-review.

These checks do not establish real SigNoz/Langfuse service acceptance, production authentication, external storage-side Agent-filter enforcement, deployment, capacity or broad cross-browser compatibility. Supported MCP/Skill history scans remain bounded at 10,000 candidate Spans and reject incomplete/unstable windows. No dependencies, migrations, branch protections or workflow configuration changed. Hosted CI status is recorded separately on the PR; local success does not imply hosted CI passed.
