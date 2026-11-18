# Walkthrough design review and registry import

This follow-up was reviewed with `frontend-design` and tested on the uncommitted working tree based on `historical-source-revision-redacted`. [evidence.json](evidence.json) identifies every changed production file by SHA-256 and binds the captures to browser readback and independent OceanBase seekdb queries.

## Changes verified

- A continuous rounded spotlight replaces four rectangular shades. Its geometry follows an ancestor drawer's active animation. Light and dark captures no longer show square corner gaps or a displaced highlight after the drawer settles.
- First visits use the browser's supported system language; an explicitly saved interface language takes precedence. New example Agents are named in Chinese, English, or Japanese. Replay preserves existing names, including user edits.
- The MCP walkthrough opens the existing registry **Search** tab. Users search, select an official entry, inspect installation options, preview JSON, and explicitly save. Directory entries continue linking to their source projects. Pasting existing JSON remains available.
- The imported server keeps its own name. The drawer shows the actual saved server instead of incorrectly associating an unrelated registry entry with the synthetic `storage_policy` Trace. Configuration query errors remain errors and offer retry.
- After the actual rule result links back to the failed Span, the guide compares the healthy baseline (A) with the retry run (B). Users enable MCP calls and inspect B's failed call. Incomplete data cannot finish the guide; closing evidence or restoring the page returns to the appropriate step.

## Actual platform captures

The test started with zero Agents and zero model connections in a new isolated Space. It used the current source frontend, Django, AgentLens, and actual OTLP/SigNoz/OceanBase seekdb 1.4.0.0 services. No API requests were intercepted. The bundled fake-model SDK export remains synthetic, but its import, querying, saved configuration, diagnosis task, and database readback were real.

| Capture | Evidence |
| --- | --- |
| [Consent](01-consent.png) | Cancellation kept the Space empty; creation required explicit confirmation. |
| [Dashboard and localized Agent](02-dashboard.png) | `示例 Agent`, 2 source Traces and 36 source spans. |
| [MCP summary](03-mcp-summary.png) | The retry Trace has 2 MCP calls and 1 failure. |
| [MCP call](04-mcp-call.png) | The stored failed call and the next configuration-import action. |
| [Registry search](08a-mcp-registry-search.png) | Actual Official MCP Registry search for `defuddle`. |
| [Registry configuration](08b-mcp-registry-configure.png) | `Defuddle Fetch` offers a stdio package with no required credential fields. |
| [Import preview](08-mcp-import-preview.png) | Original server name and pinned command preview before saving. |
| [Saved configuration](09-mcp-config-linked.png) | Existing database readback for `defuddle-fetch-mcp-server`; no synthetic `storage_policy` config was created. |
| [Rule result](05-rule-result.png) | Actual scheduler/worker result, with 2 successful tools, 1 failure, 0 unknown outcomes. |
| [Failure source](06-failed-span.png) | The rule source selected the exact failed MCP Span. |
| [Trace comparison](10-trace-comparison.png) | A's 15 spans and B's 21 spans; unreported Token values remain unknown. |
| [Compared failed call](11-comparison-failed-call.png) | The failed B call is selected in the timeline and expanded details. |
| [Dark replay](07-mcp-replay.png) | Theme switched through the product menu; Help reused the same example Agent, Trace pair, and rule. |

The final browser run passed in 23.4 seconds. It recorded zero stdio tool-sync requests and zero model connections. Independent SQL confirmed the imported name, empty credential-key lists, no tool snapshot, a successful diagnosis on attempt 1, and the same failed Span ID in the persisted result and browser selection.

The external registry entry is verification data, not a bundled recommendation or a product dependency. No external MCP package was installed or executed by importing its stdio configuration.

## Checks and boundaries

- Modified-file ESLint, TypeScript typecheck, and the production build passed. The existing bundle-size advisory remains.
- The full frontend `npm test` command completed all 32 test blocks: 1,730 passed, zero failures.
- All 29 walkthrough Playwright fixture cases passed on the final production source. These cover three locales, first-use browser language, saved preference priority, narrow layouts, dark styling, consent, query/save errors, registry selection, diagnosis readback, comparison, pause, reload, and resume. Their API responses are synthetic.
- Ten focused backend tests passed with seekdb and SQLite explicitly disabled; their downstream services are mocked. The actual platform browser run supplies the separate integration evidence above.
- A development run was invalidated by a concurrent edit to a Tailwind-scanned test file, which triggered Vite's full-page reload. The final real run and subsequent fixture run used frozen files. No production diagnosis change was inferred from that development reload.

These are local macOS arm64/Chromium results. Hosted CI, other browsers, native release artifacts, and external MCP server execution were not validated in this follow-up. No deployment or remote Git branch was changed.
