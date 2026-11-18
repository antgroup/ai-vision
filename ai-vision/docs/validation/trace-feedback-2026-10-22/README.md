# Native trace feedback: runtime UI comparison

These screenshots compare running frontend builds with the **same controlled API response**. They demonstrate presentation and Graph interactions; they are not live SDK captures, an old-backend replay, or full platform acceptance.

| View | Before | After |
| --- | --- | --- |
| Trace summary | [Before](before-summary.png) | [After](after-summary.png) |
| Parallel model calls in Graph | [Before](before-graph.png) | [After](after-graph.png) |

## Source and environment

- Baseline archive: `historical-source-revision-redacted`.
- After archive actually served: `437ea41a7be6296fc5d48ae1a2904b4f4dde927d`. The PR commit was subsequently amended to `5507f8ed094afea5e01ec070e8ee95d38e0a5421`; both commits have identical tree `ccb2e44fef084d76b3a5486124ebc9cbb73bbc2e`, verified with `git diff --exit-code` and `git rev-parse <sha>^{tree}`.
- macOS; Playwright 1.58.2; headless Chromium `145.0.7632.6`; 1440 × 1000 CSS pixels; device scale factor 1; English (`en-US`); light theme; `Asia/Shanghai` time zone.
- Each frontend was extracted with `git archive` into a separate scratch directory. Both used the same installed dependencies and isolated Vite caches. The shared checkout was not edited. Direct Node/Vite servers used loopback ports 41988 and 41989, with `NO_PROXY=localhost,127.0.0.1` and `no_proxy=localhost,127.0.0.1`.

## Controlled state and observed results

The public trace-detail route received one intercepted `/api/v1/trace-link/detail` response using the current response contract, identically for both versions. The trace contains a root and three custom-named, overlapping child model calls (`gen_ai.operation.name=chat`). Two calls report 60 total tokens each; the third omits usage. Canonical total/input/output remain null, `reportedUsage.total.value=120`, `reportedCalls=2`, and `coverage.billableCalls=3`. The canonical environment and root `langfuse.environment` are `local`; the raw resource contains the conflicting value `ant.agent.env=stale-prod`.

- **Summary:** both versions preserve “complete total unknown” and show a known subtotal of 120. The after view also states “2 / 3 calls reported total tokens” in the header, summary badge, and usage section. The after badge wraps without clipping at this viewport. Both versions show canonical environment `local`; this pair does not demonstrate an environment regression.
- **Graph:** before renders each model call as “Span” and places the siblings on three vertical levels with five edges, including inferred sibling links. After renders “LLM”, retains custom names and model metadata, and places the three calls on the same level with only the three parent-child edges. The same automatic fit behavior is retained in both screenshots; the wider sibling layout is scaled down to fit the Graph pane.
- **Interaction:** in each version, clicking “Custom reviewer 2” opens its matching detail heading. Neither run produced a page script error or an unexpected API request. Screenshots were captured after fonts and Graph layout settled, with the pointer moved away and animations disabled.

Reproduction: open the embedded public trace-detail route with the controlled response; capture the default Chain/Preview state; select **Graph**; capture the Graph state; click the second model node and verify its detail heading. The complete synthetic response is preserved in [fixture.json](fixture.json). The capture process did not modify repository source or the screenshot pixels.

This directory deliberately archives four selected comparisons, their synthetic fixture and this provenance note as review evidence; transient test logs and other screenshots are not included. Coverage is limited to this controlled English/light Chromium comparison; broader locale/theme tests and live-provider evidence are recorded separately in the PR.

## Independent review and deployment boundary

The backend and frontend reviews compared the baseline above with code commit `5507f8ed094afea5e01ec070e8ee95d38e0a5421`. No required code fixes were identified. Reviewers separately executed 43 focused backend tests and 45 focused frontend tests, all passing. This technical review does not confer maintainer approval.

Deploy the frontend and backend together: the new frontend requests canonical fields that the old backend rejects as unknown. Mixed frontend/backend versions were not validated. Older SigNoz versions remain supported through the environment-facet compatibility path, subject to the existing 10,000-trace verified-scan limit. Actual deployed SigNoz behavior and production scan costs remain unverified.

GitHub Actions runs `39777676803` and `39777676320` in the former development repository did not start due to an account payment/spending-limit restriction. The test results above come from local checks.
