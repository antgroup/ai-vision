# Selected-trace action bar validation

Date: 2026-10-28. Follow-up to product PR #132, based on `historical-source-revision-redacted`. Source hashes and screenshot identities are in the [matching receipt](trace-selection-actions-2026-10-28.json).

The trace list now labels its first column **选择 / Select / 選択**. Unselected and empty lists have no diagnosis action row. Selecting traces reveals an in-flow bottom action bar with the selected count, Run diagnosis, and an accessible clear control. The bar is outside the horizontally scrolling table and follows the actual main scroll container. Its actions can accommodate a future comparison feature; comparison itself is not implemented here.

The [final merge review](trace-diagnosis-final-review-2026-10-28.md) records latest-main integration, the final inline-result cleanup, renewed three-locale real runs and the final build/test totals. This report preserves the earlier layout evidence.

## Checks

- Frontend full suite: **1,258 passed**, zero failures or skips. Changed-file ESLint, typecheck and production build passed. The existing large-chunk warning remains.
- Targeted selection regression: **32 passed**; locale checks: **11 passed**. The new selected-only and clear/pagination assertions failed before implementation and passed after it.
- Independent six-file diff review found no actionable defect. It reviewed layout placement, localized accessible names and existing selection scope/cap behavior. It did not independently run the browser or backend.

## Real browser and backend

The built-in browser used the same prepared Vite/Django/AgentLens/collector/OceanBase services at `localhost:5175`; backend source was unchanged. Safe synthetic inputs were used throughout. Eighteen additional source traces (36 spans) were ingested through the real collector and read back, giving 22 recent traces / 44 spans for pagination and scroll checks. Original application replay rows were not modified.

- Empty Agent: no action row. Populated list: visible Select header; no Run diagnosis button before selection.
- Two selected numeric traces opened the existing saved-diagnoser dialog from the new bar. The actual POST contained exactly those IDs, one saved diagnoser UUID and one request UUID; no duplicate candidate list was fetched.
- Task `8d53a5273fee409cb768af99bc2da2f0` completed with two persisted numeric records (one good, one bad), source_complete=true, zero invalid/projection-failed inputs and zero reserved model requests. Job: `b942d34f03c144eea4cf1de2e2781f16`. No external model request was made.
- Selecting ten traces disabled the other ten checkboxes on the 20-row first page. On page two, the count remained ten and the diagnosis dialog still received those ten IDs. Opening that dialog did not execute the ten-trace selection.
- Clear removed the action bar and re-enabled the current page's checkboxes. Switching to Session removed the bar; returning to Trace did not restore the old selection.
- On the 1440×1000 desktop, the long-list bar bottom was 984 pixels while scrolling the list. At the end of the main scroll range, the bar occupied y=918–976 while pagination occupied y=841–877: no overlap. The scroll container reached scrollTop=1066 with scrollHeight=2002 and clientHeight=936.
- English Select/count/action and Japanese Select/count/action rendered through actual language switching. The 390×844 Japanese bar was about 342 pixels wide at x≈24; clientWidth and scrollWidth were both 340. Both action controls remained inside the viewport. The table retained its independent horizontal scrollbar.

Screenshot evidence shows actual local UI with synthetic text. Chinese dark, English light, and Japanese light surfaces were inspected. Narrow checks use a resized built-in browser, not a separate physical mobile device.

At 320×844, the English bar wrapped to 91 pixels tall, measured 273 pixels wide at x=16, and had equal 271-pixel client/scroll widths. Both controls remained inside the viewport.

## Screenshots

- [selected-en-light-320.jpg](assets/trace-selection-actions/selected-en-light-320.jpg) — 320 × 844
- [selected-en-light.jpg](assets/trace-selection-actions/selected-en-light.jpg) — 1440 × 1000
- [selected-ja-light-narrow.jpg](assets/trace-selection-actions/selected-ja-light-narrow.jpg) — 390 × 844
- [selected-ten-zh-dark-bottom.jpg](assets/trace-selection-actions/selected-ten-zh-dark-bottom.jpg) — 1440 × 1000
- [selected-ten-zh-dark-scroll.jpg](assets/trace-selection-actions/selected-ten-zh-dark-scroll.jpg) — 1440 × 1000
- [selected-zh-dark.jpg](assets/trace-selection-actions/selected-zh-dark.jpg) — 1440 × 1000
- [unselected-zh-dark.jpg](assets/trace-selection-actions/unselected-zh-dark.jpg) — 1440 × 1000

## Scope

This is frontend layout/selection validation on a prepared macOS source environment. No new backend implementation, provider call, Linux/deployment validation or hosted-CI success is claimed. The earlier diagnosis persistence/history report remains the evidence for those unchanged backend fixes. Test resources are tracked and retained for review.
