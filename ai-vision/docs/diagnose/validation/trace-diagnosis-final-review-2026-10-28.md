# Trace diagnosis final review and compact layout

Date: 2026-10-28. Product PR #132. Tested runtime source: `historical-source-revision-redacted`, including main `historical-source-revision-redacted` (PR #129). The [receipt](trace-diagnosis-final-review-2026-10-28.json) identifies source hashes, tree identities, actual tasks and screenshots. This report supersedes the inline-result layout captures in the earlier [diagnosis report](trace-diagnosis-2026-10-28.md) and [selection-bar report](trace-selection-actions-2026-10-28.md); their original persistence, history and selection evidence remains source-bounded.

## Product boundary and design

Trace list/detail run one saved diagnoser or configured specialist. There is no independent Quick Check entry, scenario selector, model override, frozen-condition retest or comparison UI. Configuration stays on Diagnosers; retained results stay on the Quality Board. Legacy server routes, receipts and stored history metadata remain compatible.

The first final-review screenshot was newly captured from the real runtime, but exposed remaining old inline scope/budget grids, repeated advice and technical controls. Those are removed from the run dialog. Results retain their outcome, evidence and original Trace/diagnosis-detail links. Duplicate reasons matching the summary display once; independent reasons remain visible. Source-incomplete warnings, skipped reasons, failed checks and task failure messages stay visible. Full execution audit remains in existing task details.

The frontend-design pass uses a compact run toolbar, an always-visible selected count with expandable exact IDs, and an expandable configuration explanation. Management/refresh stay on a quiet secondary row. Result filtering aligns with the heading, result articles use dividers, and a single page has no disabled pagination row. Existing neutral product tokens and PingFang/system fonts are retained (13 px text, 12 px metadata); no dependency or decorative palette was added. Controls wrap naturally on narrow screens rather than shrinking their text.

## Review and automated checks

Independent review covered latest-main integration, the saved-only/result cleanup, compact controls and final detail-label changes. No remaining P1/P2 finding was identified. These coding-agent reviews are not a maintainer approval.

- Final frontend full suite: **1,279 passed**, zero failures, cancellations or skips. Modified-file ESLint, formatting, typecheck and production build passed. The existing large-chunk warning remains.
- Result-specific suite: **89 passed**; locale checks: **11 passed**. The three new DOM regressions failed before their respective changes and passed afterward. They cover removal of audit panels/repeated advice, distinct evidence and unconditional detail access, incomplete sources/selectors, recordless skips, and selected-ID disclosure state while changing the diagnoser.
- Merged-tree Django query + diagnosis combination: **234 passed**, zero failures or skips. Existing warnings concern app-initialization database access and absent local static files; mocked maintenance/400/503 logs are intentional test inputs.
- AgentLens affected-path suite: **685 passed / 32 skipped** during this review. Main #129 and the final frontend changes leave that tested AgentLens source unchanged. Skipped cases require optional isolated DB fixtures.
- Independent detail/history smoke included nine existing offline regressions; compact/recovery/filter review included eight targeted DOM cases. Immutable feedback/read-back and retained frozen evidence remain intact.

## Actual frontend/backend runs

The built-in browser and isolated dev-browser headless Chromium used the prepared local Vite → Django → AgentLens → collector/OceanBase services at `localhost:5175`. Each final run selected the same two safe arithmetic traces and the saved numeric specialist. Every batch persisted **one good and one bad diagnosis record**. Direct database read-back confirmed one accepted receipt, two records and one task span for each batch.

| Interface language | Task | Job | Browser |
| --- | --- | --- | --- |
| Chinese | `a21757f132ff4048a4f36a54af375b1c` | `6247b1ee6d234792b83a63d69ca3f2c9` | Codex built-in browser |
| English | `15ba7b2486e84bfaa57b7ae5ecf31c5b` | `ec42a1002b2749749fa430a3e5b53d55` | dev-browser isolated headless Chromium |
| Japanese | `b817f53f014946208581bdb11b56361f` | `b8804b6f640f4d41b98b04ce83058ec7` | dev-browser isolated headless Chromium |

Exact selected IDs and the Decimal applicability explanation remain accessible through native disclosures. Real bad-result filtering returned one record, and clearing it restored both. Existing detail navigation retained the same Space, Agent, task and record identity; its original-source action opened the correct Trace. The observed copy, expand/collapse and original-Trace controls in the existing drawer were also localized using existing resources and rechecked in English and Japanese without feedback writes. Frozen saved names and generated evidence keep their recorded language.

## Measured layout and screenshots

- Built-in browser, Chinese, 1218 × 848: dialog **1024 × 608**, both results visible, no internal scrolling.
- Isolated headless Chromium, English, 1440 × 1000: dialog **1024 × 608**; at 572 × 848: **540 × 600**, both results visible without scrolling.
- English 320 × 844: dialog width **288 px**; its 773 px content uses about **13 px** of vertical scrolling. Dialog edges and controls fit horizontally.
- Japanese dark, 390 × 844: dialog **358 × 701**, no internal scrolling. These are desktop browser viewport checks, not physical mobile-device tests.

Resize checks waited for the requested viewport and stable animation/layout before asserting geometry. Immediate pre-settlement captures briefly showed old dimensions and were superseded; no persistent overflow defect was found or masked by a product change.

- [saved-diagnosis-result-zh.jpg](assets/trace-selection-actions/saved-diagnosis-result-zh.jpg) — 1218 × 848
- [compact-result-en-desktop.jpg](assets/trace-selection-actions/compact-result-en-desktop.jpg) — 1440 × 1000
- [compact-result-en-572.jpg](assets/trace-selection-actions/compact-result-en-572.jpg) — 572 × 848
- [compact-result-en-320.jpg](assets/trace-selection-actions/compact-result-en-320.jpg) — 320 × 844
- [compact-result-ja-390-dark.jpg](assets/trace-selection-actions/compact-result-ja-390-dark.jpg) — 390 × 844

## Validation boundary

No external model request was made: the saved numeric specialist uses a deterministic rule. Missing-model rejection and the earlier 61 HTTP case classes remain covered by prior real runs; model prompt freezing and budget hooks use offline/mocked tests. The earlier large-source and fixed-task history evidence applies to the unchanged backend. Projection compaction covers supported-projector failures, not arbitrary oversized successful/CUSTOM snapshots; retention and the record-candidate cap still apply.

Coverage is a prepared macOS source environment, not Linux, cold start, Kubernetes, released images or hosted CI. GitHub Actions did not start because account payment/spending-limit restrictions were reported. The earlier unresolved `127.0.0.1` cookie-host observation remains outside this change; final runs used `localhost`. Synthetic resources remain tracked outside Git for review; original replay inputs remain untouched.
