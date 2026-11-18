# UI reading simplification: local validation

Date: 2026-11-12. This record covers the frontend patch based on `historical-source-revision-redacted`. The source/test diff SHA-256 is `d76998c53490c8aceb0192f6104f7657e8a36d0ab778a58245fc33d6788fc2e6`; screenshots and this evidence record were added afterward.

## Change and review

- Move MCP and memory explanations into keyboard-accessible help popovers beside the page breadcrumb. Retain metric help, the glossary, documentation links, and maturity markers.
- Show catalogue counts when filtered or partially loaded, and Reset only when it has work to do. Keep no-data percentiles as `—`.
- Put the framework trend selector inside the chart header; preserve an accessible chart heading and all query, cancellation, error and encryption behavior.
- Remove repeated model-connection and plugin headings/descriptions. Retain credential-change, permission, discovery and execution-policy constraints.
- Stack the account name and email in settings, allow wrapping, and name the icon-only edit/copy actions. Keep English, Simplified Chinese and Japanese copy aligned.
- Update the lifecycle test double to render the chart header controls and assert recovery actions independently of those controls. No backend, ingestion, query, dependency or schema changes.

## Real-provider and platform acceptance

A new batch used PowerContext 1.5.0 with its real SDK/server application over a local ASGI HTTP transport and SQLite, and Baidu Qianfan `deepseek-v4-pro-0813` for all eight model completions. The model service was remote and real; the memory service was isolated locally. The controlled prompts are warehouse-policy examples, not customer data.

- Eight provider requests succeeded on the first attempt; no failed attempts or retries.
- Eight original traces / 176 spans were admitted through the platform OTLP endpoint with zero rejected spans. API read-back matched span IDs, parents, timestamps, status and usage, plus 696 PowerContext attributes.
- Search hit distribution: three runs with 0 hits, two with 1, two with 2, and one with 3. Six runs enabled automatic capture; two disabled it.
- Exact model usage: 1,528 input + 36 output = 1,564 Token. The dashboard shows 8 traces, 176 nodes and 8 model calls. Memory overview shows 14 deduplicated business operations and 62.5% search hit rate (5/8).
- On both 1440 × 1000 and 390 × 844 Chromium viewports, all eight single-trace details and both sides of four comparisons matched their search hit counts. Positive results linked to the correct recall and showed the expected number of content items; zero-hit results did not offer a misleading recall-content action.
- The production build displayed eight recent model calls and a loaded activity chart with eight calls. Actual query POSTs used the `payloadVersion / kid / iv / encryptedPayload` envelope, without a plaintext SQL/query payload. Both baseline and candidate were checked against the same backend over loopback HTTP.
- Help Enter/Escape/focus return, filtered counts, search and sort reset, trend switching, session expansion, model-form dismissal and sandbox navigation passed. Final real-data runs recorded no failed API responses, 429s, uncaught page errors or document-level horizontal overflow.

Environment: macOS ARM64, local native platform backend and AgentLens, isolated seekdb/SigNoz/collector containers, and production Vite builds. Baseline and candidate used the same fixed time window and ingested batch. The manifest records the exact dependency versions, trace IDs, assertions and image hashes.

## Local checks

Run from `ai-vision/vfe/`:

| Check | Result |
| --- | --- |
| `npm run lint:ci` | Passed |
| `npm run typecheck` | Passed |
| Prettier check of changed TS/TSX/JSON files | Passed |
| `npm test` | 1,722 Node tests passed |
| `npm run build` | Passed; existing bundle-size warning remains |
| Selected Playwright suites | 76 passed |
| `git diff --check` | Passed |

Playwright selection: `memoryObservability`, `labsLoadMore`, `labsMetricHelp`, `labsStatusEvidence`, `sandboxConnections`, and `nativePrimaryMetrics`. These tests use API fixtures and cover three locales plus responsive/help/status behavior. They supplement, rather than replace, the independent real-provider/backend checks above.

## Screenshots

Every before/after pair below comes from the same real backend and batch. The before build is the pinned main revision; the after build contains this patch. Screenshots are direct browser captures with settled dialogs/charts, not illustrations. Trace detail/compare images are supplemental regression evidence; those views are unchanged by this patch.

| View | Before | After |
| --- | --- | --- |
| desktop · memory | ![Before](images/ui-reading-20261112/desktop-memory-before.png) | ![After](images/ui-reading-20261112/desktop-memory-after.png) |
| desktop · dashboard | ![Before](images/ui-reading-20261112/desktop-dashboard-before.png) | ![After](images/ui-reading-20261112/desktop-dashboard-after.png) |
| desktop · dashboard-activity | ![Before](images/ui-reading-20261112/desktop-dashboard-activity-before.png) | ![After](images/ui-reading-20261112/desktop-dashboard-activity-after.png) |
| desktop · settings | ![Before](images/ui-reading-20261112/desktop-settings-before.png) | ![After](images/ui-reading-20261112/desktop-settings-after.png) |
| desktop · model-form | ![Before](images/ui-reading-20261112/desktop-model-form-before.png) | ![After](images/ui-reading-20261112/desktop-model-form-after.png) |
| desktop · sandbox | ![Before](images/ui-reading-20261112/desktop-sandbox-before.png) | ![After](images/ui-reading-20261112/desktop-sandbox-after.png) |
| mobile · memory | ![Before](images/ui-reading-20261112/mobile-memory-before.png) | ![After](images/ui-reading-20261112/mobile-memory-after.png) |
| mobile · dashboard | ![Before](images/ui-reading-20261112/mobile-dashboard-before.png) | ![After](images/ui-reading-20261112/mobile-dashboard-after.png) |
| mobile · dashboard-activity | ![Before](images/ui-reading-20261112/mobile-dashboard-activity-before.png) | ![After](images/ui-reading-20261112/mobile-dashboard-activity-after.png) |
| mobile · settings | ![Before](images/ui-reading-20261112/mobile-settings-before.png) | ![After](images/ui-reading-20261112/mobile-settings-after.png) |
| mobile · model-form | ![Before](images/ui-reading-20261112/mobile-model-form-before.png) | ![After](images/ui-reading-20261112/mobile-model-form-after.png) |
| mobile · sandbox | ![Before](images/ui-reading-20261112/mobile-sandbox-before.png) | ![After](images/ui-reading-20261112/mobile-sandbox-after.png) |
| desktop · mcp | ![Before](images/ui-reading-20261112/desktop-mcp-before.png) | ![After](images/ui-reading-20261112/desktop-mcp-after.png) |
| desktop · skill | ![Before](images/ui-reading-20261112/desktop-skill-before.png) | ![After](images/ui-reading-20261112/desktop-skill-after.png) |

Real single-trace recall:

![Single Trace](images/ui-reading-20261112/desktop-single-double-cobalt-after.png)

Real comparison on a mobile viewport:

![Trace comparison](images/ui-reading-20261112/mobile-compare-2-A-after.png)

## Boundaries

- Mobile evidence is Chromium viewport emulation; no physical iOS/Android or Safari acceptance was run.
- The live UI pass used Simplified Chinese/light theme. Other locales/themes are covered by the named fixture suites, not new provider runs.
- This PowerContext batch contains no MCP-tool or Skill calls. Those pages correctly show empty observations; populated/pagination/error states are covered by fixture tests.
- The new model connection form and sandbox policy UI were inspected against the real backend; this pass did not execute model discovery, connection validation, sandbox execution or a diagnosis job.
- Loopback HTTP is a browser secure context. This live run proves encrypted production requests, not a fresh insecure-origin AES fallback acceptance; the fallback/recovery unit tests passed.
- These are local prepared-environment results, not a cold install, released image, hosted deployment or CI claim. Provider keys, cookies, raw model transcripts and private runtime logs are excluded from the PR.

[Machine-readable evidence](images/ui-reading-20261112/evidence.json).
