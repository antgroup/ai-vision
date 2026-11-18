# Frontend UI Quality Guide

Use this guide when changing product copy, localization, trace presentation, or usage summaries in the active `vfe/` frontend. Follow the [frontend guidelines](../vfe/AGENTS.md) and [frontend development skill](../.codex/skills/ai-vision-frontend/SKILL.md). This guide records reusable rules; individual acceptance reports own model IDs, environment details, screenshots, and results.

## Modal Size and Viewport Rules

Use `vfe/src/components/dialogs/DialogContent.tsx` for modal content and
`AlertDialogContent.tsx` for confirmations. Keep the shadcn primitives unchanged.
Choose a size by the task, not by the page or an arbitrary breakpoint:

| Size | Desktop maximum | Use |
| --- | --- | --- |
| `compact` | 448 px | Confirmations, sharing, one-field prompts |
| `form` | 576 px | Model/sandbox connections, keys, member invitations |
| `detail` | 768 px | Long details, pricing settings, onboarding, search |
| `workspace` | 1024 px | Import browsers, diagnosis and connection selection |
| `fullscreen` | Viewport minus 32 px | Expanded code editors and Trace variable browsing |

All sizes retain 16 px on each side of the viewport and a maximum height of
`100dvh - 32px`. Content-height dialogs scroll when needed. Use `fillHeight` for
multi-pane workspaces with their own scrolling; it fills up to 832 px within the
same viewport limit. Fullscreen editors use the available dynamic viewport height.
Do not add per-dialog width/height overrides. Content-specific spacing, colors
and inner scrolling remain with the consumer. Non-modal popovers and side sheets
keep their separate layout rules.

Keep every primary/cancel action reachable at narrow and short viewport sizes.
For split panes, stack or otherwise adapt them below the desktop breakpoint;
shrinking the outer dialog alone must not crop a fixed-width inner workspace.
Check actual bounding boxes, long content scrolling, Escape and focus return.
The shared content wrappers return focus to the opening control (or its menu
trigger) for controlled dialogs; explicit consumer focus handlers take precedence.

## Product Copy and Localization

Classify visible text before changing it:

| Text owner | Treatment |
| --- | --- |
| Product labels, actions, explanations, placeholders, tooltips, accessible names, loading/empty/error states | Use locale resources. Ordinary English in a Chinese screen needs review even when it appears only on hover or in a drawer. |
| Established product/protocol names and technical identifiers, such as OpenTelemetry, Langfuse, MCP, API, model IDs, or `trace_id` | Preserve their spelling. Localize the surrounding explanation and provide a readable label where needed. |
| User names, custom span names, prompts, model responses, raw attributes, URLs, commands, and copied/exported payloads | Preserve source content. Localizing the surrounding UI does not authorize rewriting these values. |
| Legacy integration keys, aliases, parser fields, and sorting/filtering identifiers | Preserve compatibility. Change the display mapping instead of renaming the underlying contract. |

- Use public product names in product-owned labels, default examples, and onboarding copy. Check both source strings and rendered UI. Preserve user telemetry and legacy compatibility fields.
- In Chinese product copy, put spaces between Chinese text and Latin words or numbers: `模型 ID`, `共 3 次调用`, `输入 Token`. Apply this to interpolated messages too; never run a spacing replacement over raw data, code, or URLs. Use each locale's own grammar rather than copying Chinese spacing rules into Japanese.
- Replace unexplained symbols or abbreviations with words. Token summaries need explicit input, output, and total labels; a lone `Σ`, arrow, or depth prefix is insufficient. Keep useful technical terms with a localized explanation rather than inventing misleading translations.
- Update `zh-CN`, `en-US`, and `ja-JP` together. Preserve interpolation names, plural behavior, arrays, and rich-text tags. A fallback that renders successfully does not prove that translation is complete.
- For official documentation links, reuse [documentation.ts](../vfe/src/lib/documentation.ts) with the resolved interface language. The sidebar opens the localized introduction; feature cards keep their specific guide destinations. Use a native link, safe new-tab attributes, a localized accessible name, and a tooltip when an icon is shown without its label.
- Keep action labels distinct from status labels (`Enable` versus `Enabled`), and check English counts at both one and multiple items. Audit generated replay summaries and partial-failure messages as well as static page headings.
- Use shared label mappings for recurring table columns. Localized labels must not become sort keys, filter values, route parameters, or telemetry identifiers.
- Keep examples executable after translation. If a search parser accepts `Turn 3`, localize its explanation but do not suggest a translated search syntax that the parser does not recognize.
- Review compact and secondary surfaces: badges, chart legends, menus, copy feedback, validation errors, receipt/share pages, onboarding instructions, and detail panels. Translate known built-in guide text by exact identity; preserve custom guide text.
- Pass the resolved interface language to timestamp formatters. Check date, weekday, and AM/PM output after switching language; do not change timestamp precision, time-zone policy, or exported values just to localize the display.
- Suppress duplicate summary metadata only when the key and value match the displayed summary. Keep conflicting values and custom fields available for inspection.
- Adding localization must preserve React hook order and stable component identity; do not call translation hooks conditionally or inside ordinary formatting callbacks.

## Observability View Enhancements

Reuse [LabsSection.tsx](../vfe/src/components/labs/shared/LabsSection.tsx) for user-input, decision, MCP, and Skill views. Its two templates serve existing summary and detail surfaces:

| Template | Use | Presentation |
| --- | --- | --- |
| `variant="summary"` | Trace-level user-input, decision, MCP, and Skill summaries | 12 px padding, compact wrapping title and metrics, description available through clickable help |
| `variant="detail"` | Decision, MCP, and Skill span details | 12 px padding, compact wrapping title and metrics, description available through clickable help |

- Both templates share the icon, wrapping header, border, background, and `--color-labs` tokens in light and dark themes. Keep user input first, followed by canonical output and related summaries in the native overview. Preserve original input, call information, attributes, and events in clearly labeled reversible sections.
- Each card declares a typed `feature`. The purple **Enhanced view** marker beside its heading opens that feature's documentation in the [observability view enhancements section](https://aivision.mintlify.site/en/guides/observability-enhancements). Reuse [observabilityViewDocs.ts](../vfe/src/components/labs/shared/observabilityViewDocs.ts) for `zh-CN`, `en`, and `ja` routes and the English fallback. Preserve keyboard focus, a feature-specific accessible link name, and safe new-tab attributes.
- The purple marker identifies the view category. It does not indicate success, failure, confidence, data completeness, or answer quality; keep those meanings in their existing status indicators.
- The neutral Beta badge is controlled separately by `beta` and describes feature maturity. It is not the category marker or a judgment of the trace's data quality and provenance. Keep headings, category links, and maturity badges as separate elements so titles remain readable and wrapping works on narrow screens.
- Preserve source navigation, masking, and original telemetry. Skill observability uses these templates and the existing localized observability overview documentation. Do not create empty cards or placeholder sections for unimplemented views.

## Dense Enhanced Content

- Start expanded record groups and histories with at most five records. Show the displayed/total count whenever records are withheld, and provide load-more, show-all, and collapse actions. Fully displayed small lists need no redundant pagination row.
- Apply exception filters and explicit chronological/duration sorting to the complete data before applying the display limit. Keep aggregate counts separate from matching counts, preserve unknown durations, and retain stable source IDs and sequence labels after sorting.
- Reset trace-scoped filters and display windows on a trace/Space/source change; preserve same-trace refreshes and source selection when changing content format. Selecting a visible call-list result must not clear the active search.
- The native toolbar owns Enhanced versus Original content mode. Original mode has a subordinate Tree/JSON format choice and suppresses all enhancement cards. Preserve every original attribute and resource value, including competing IO sources and JSON-looking strings, without recursive parsing or rewriting.
- Use container-responsive labeled detail fields to avoid nested horizontal table scrolls. In narrow windows, selecting a source gives its content the available height; a call-list toggle preserves navigation without a fixed 40%/65% split.
- Use typed Markdown, JSON, and code/log presentations with exact original-value copy. External media requires an explicit load action scoped to that URL; changing the URL must not reuse permission for a previous source.
- File-change previews focus contextual unified diffs and retain recorded patches and complete before/after values. Consume only successfully projected change fields; preserve unused custom fields. Bound comparison work, disclose coarse replacement fallback, and preserve line endings and final-newline distinctions.

## Metric Help and Sampling

- Use [LabsMetricLabel.tsx](../vfe/src/components/labs/shared/LabsMetricLabel.tsx) for percentile help in Labs overview strips, cards, and tool-table headings. Reuse the existing Tooltip, neutral help icon, localized accessible name, visible keyboard focus, Escape dismissal, and viewport collision handling. Activating help inside a clickable card must not open the card.
- Explain the actual population as well as the percentile. P50 and P95 are duration thresholds covering at least 50% and 95% of counted calls, respectively; they are not success rates. The current nearest-rank calculation selects an observed value without interpolation. A single call yields identical P50 and P95, and small samples can make P95 equal the maximum.
- A **Slowest P50/P95** overview first calculates each Skill's or MCP server's percentile, then shows the largest result and its owner. Do not describe it as a percentile pooled across all entities. State the selected time/Agent scope, inclusion of failed calls, and any incomplete-scan warning.
- Skill durations describe the observed span. A derived SKILL.md read measures its containing tool call; it does not establish the duration of an entire Skill task. Preserve the source values and avoid adding an execution guarantee through UI wording.
- No calls means no latency distribution or error-rate denominator: show a neutral unavailable value rather than `0 ms` or a green `0%`. A zero value in a record with calls remains zero; do not convert it to unknown. A trace summary with any missing call duration must not show a complete duration by substituting zero.
- Keep metric help available when a dynamic owner name replaces a subtitle. Verify the actual translated glyphs, help buttons, and drawer actions against their clipping ancestors in both themes and narrow layouts; a complete six-item strip must not gain a sparse extra row.

## Model Calls: Operation, Model, and Original Name

An SDK name such as `chat vendor/model` combines an operation and a model identifier. Present them separately so users can distinguish what happened from which model was used.

| Source case | Presentation |
| --- | --- |
| A conventional chat name backed by model/operation metadata | Localized operation title, such as `模型对话`, plus a separate neutral model badge |
| A conventional embedding name backed by model/operation metadata | Localized operation title, such as `向量化`, plus a distinct operation icon and neutral model badge |
| A custom name, such as `chat retrieval step`, even on a model span | Preserve the custom title; show known model metadata separately |
| An Agent turn, tool, MCP, or decision span | Preserve its specialized meaning and title; a model attribute alone does not make it a model request |
| Missing or placeholder model metadata | Do not guess a model from free text or show `unknown` as a genuine model identifier |

- Reuse [modelSpanPresentation.ts](../vfe/src/components/traces/shared/modelSpanPresentation.ts). Explicit operations take precedence. In particular, `gen_ai.span.kind=CHAT` may describe an entire Agent turn, and `invoke_agent` / `execute_tool` must not become model requests.
- Model-suffixed names must match the **complete** name constructed from an operation alias and a reported response/request/observation model. Empty names and bare supported operation aliases can also receive a localized title once the operation is established. Do not strip arbitrary `chat` / `embed` prefixes or custom suffixes; keep aliases and type fallbacks in the shared resolver.
- Prefer a known response model, then a known request model, then the observation model. A placeholder response value must not hide a known request model.
- Apply the same display resolution to Chain, Graph, Timeline, details, and search. Where search exists, retain matches for the original name, model ID, and localized title; preserve selection and specialized MCP/decision titles.
- Verify each data-source UI separately, including compatibility observation views. Switch views with an active localized search: a title found in Chain must remain searchable in Timeline. Custom model-call titles still need their known model metadata.
- Keep the original span name available in details or a tooltip. Display improvements must not mutate SDK payloads, raw attributes, copy/export content, span IDs, or parent relationships.
- Reuse [TraceBadge.tsx](../vfe/src/components/traces-otel/shared/TraceBadge.tsx). Model identifiers use neutral styling; status colors retain their status meaning. Keep error/fallback badges distinguishable from model metadata.
- Verify long identifiers, ellipsis, full hover titles, selected borders, and both themes. Check actual glyph visibility and node bounds: an element can exist in the DOM while its text is clipped. Keep rendered node size consistent with graph layout allocation.

## User Input: Message Evidence and Source Context

- Keep the user-input summary separate from canonical trace/observation IO. Root parameters such as `selected_skills` remain available as original input; a display summary must not replace them with a descendant prompt or change copied/exported telemetry.
- Reuse [traceUserInput.ts](../vfe/src/components/traces/shared/traceUserInput.ts) for explicit user-role and supported LangChain human messages. Do not infer a user question from arbitrary strings, system prompts, assistant tool calls, tool results, or parameter objects. Respect normalization provenance: synthesized `gen_ai.input.messages` alone cannot prove an original user message.
- Resolve operation/kind precedence consistently with the backend before excluding tool, retrieval, and embedding inputs. Do not implement a second, simplified classification in a view adapter.
- Distinguish trace-level input from observation input. A child can update `langfuse.trace.input`; only canonical projection resolves competing updates. Do not label that value as the root observation's input or offer an unproven source-call link.
- Preserve message order and the last explicit user message, including empty or non-text content. Show previous user messages separately, and label the source; user-role messages can include history or internal instructions, so do not claim they necessarily identify the current end-user request. Deduplicate equivalent message sequences without modifying their content.
- Offer a bounded preview, expansion, exact-content copy, and navigation to verified source calls. Preserve structured content on copy; do not automatically fetch remote media in a summary. Show an explicit absence state for loaded data and short-circuit payload extraction in masked views.
- Verify both the overview and the selected root detail. Include long system prompts before user messages, assistant/tool messages after them, multiple user turns, image-only last messages, source navigation with an active search, and the reported viewport. Fixture evidence does not establish real SDK/provider ingestion.

## Usage: Unknown, Zero, and Subtotals

Usage presentation must communicate measurement completeness, not just produce a number.
These are acceptance criteria for changes, not a claim that every legacy view has migrated to nullable usage. Inspect the affected consumer's numeric rollups, fallback, and visibility guards.

| Evidence | Required interpretation |
| --- | --- |
| Provider explicitly reports `0` | Known zero |
| Provider omits a field, including output usage on an embedding response | Unknown; do not substitute zero |
| Provider reports a total | Preserve that total, including zero; do not overwrite it with an incomplete sum |
| Both input and output are reported on a model call, with no explicit total | The leaf usage helper can derive their sum |
| Only some calls have usage | A proven known subtotal may be shown, but the complete total remains unknown |
| Nested or ambiguous generations | Keep the aggregate unknown until ownership and deduplication are established |

- For native list and detail summaries, use the backend's nullable `usage`, `reportedUsage`, and `coverage` evidence (under `semantics` in details). Do not reconstruct these values from the spans loaded by the browser or turn a known subtotal into a complete total or a `100%` claim.
- Show `reportedUsage.total.value` as a known subtotal only when the canonical total is unknown and at least one call reported a total. Display the reported-call count against `coverage.billableCalls`, independently of trace completeness. When `unknownReason=ambiguous_nested_generations`, explicitly state that reported values may overlap; even all calls reporting usage does not establish an additive total. Do not sum Agent/chain rollups together with child model calls.
- Use [extractReportedSpanUsage](../vfe/src/components/traces-otel/spanUsage.ts) for nullable model-call measurements. Its leaf derivation rule is not permission to reconstruct canonical session/trace totals from arbitrary fields.
- Inspect `aivision.normalization.sources` before treating standard usage attributes as provider measurements. The map records both direct copies and derived values: verify direct copies against their original measurements, including a reported total of zero. Legacy derived fields may omit cache or reasoning buckets; preserve unknown unless canonical per-observation evidence establishes the value. Matching model-call counts cannot establish measurement completeness.
- Use the same nullable measurements in Graph and Chain. Test provider totals that differ from input plus output, explicit zero totals, and partially reported embedding usage. Visibility guards must distinguish a known zero from an unreported model call without adding usage panels to ordinary spans.
- Parent tooltips describe reported subtree subtotals and omit percentages. The existing tree rollup adds own and descendant usage without deduplicating parent aggregates; it is not a canonical trace total. Leaf tooltips preserve missing fields and omit percentages when completeness or the denominator is unknown or zero.
- A trace with no model calls and no usage measurements should not gain a meaningless unknown-usage panel. An actual failed model call with missing usage still needs the unknown state.
- Legacy responses retain a compatibility fallback only when the entire canonical usage object is absent. A present object with `total=null` must preserve unknown. Test the fallback separately; do not claim that it establishes completeness or silently change shared numeric rollup behavior while adjusting labels.
- Metric summaries must distinguish total calls from calls with reported usage. Missing price or usage evidence must not become zero cost.

## Verification and Evidence

Choose checks by the changed behavior, following the [contribution guide](../../CONTRIBUTING.md). Documentation-only edits need link, command, metadata, and diff checks; they do not require new model requests or a running stack.

For frontend changes, run the skill's modified-file lint, `npm run typecheck`, and build checks. Select existing regression entry points from `vfe/package.json`:

| Scope | Existing entry points |
| --- | --- |
| Locale integrity and language switching | `npm run test:i18n` |
| Usage semantics, original data, and export evidence | `npm run test:trace-evidence` |
| Span presentation, tree tooltips, and model-name edge cases | `npm run test:decision` |
| Rendered model labels, layout, search, and selection | `npm run test:e2e -- tests/e2e/modelSpanPresentation.spec.ts` |
| Broader frontend regression | `npm test` and affected Playwright specifications |

These tests establish their own scope. A passing locale test does not prove that all visible copy or every locale's key parity is covered. Fixture-based browser tests do not establish real provider or platform acceptance. Add focused regressions for semantic failures; do not add tests that merely repeat a copy edit.

For visible trace changes, inspect the affected views in Chinese, English, and Japanese, in light and dark themes. Include chat, embedding, error, missing usage, reported zero, custom names, and long model IDs as relevant. Verify search only in views that offer it; Graph still needs selection and layout checks. Record actual viewport sizes and browser engines rather than implying mobile or cross-browser coverage.

Exercise the longest translated labels at the narrow tested viewport. Check each control and text box against its clipping ancestors; an outer container's `scrollWidth` and a visible DOM element can both pass while an inner `overflow-hidden` region hides the label. Intentional table overflow is acceptable when scrolling actually exposes every column.

Interact as a user would. Wait for loading and animations to settle; move away from or dismiss an existing tooltip before opening another. Do not use forced clicks or weaken assertions to conceal an overlap or hover-state failure. Inspect screenshots in addition to assertions.

When real models are requested, follow the [framework E2E real-model UI acceptance guidance](../agent-plugins/tests/framework_e2e/README.md#real-model-ui-acceptance). Connect the evidence end to end:

1. Record the bounded provider requests and actual results, including failures and omitted usage fields.
2. Capture the original SDK export and forward it without rewriting names or values. Describe whether instrumentation is manual or automatic and whether artifacts retain raw bytes or only a wire hash plus decoded content.
3. Compare actual platform readback with SDK and provider evidence: IDs, parents, names, status, timestamps, operation/model attributes, inputs, outputs, usage, and complete embedding vectors. Require the query's completeness evidence.
4. Open the same trace IDs through real authenticated UI requests. Provider success alone, a compatibility API, or a fixture does not establish acceptance of every data-source UI.
5. Preserve local screenshots and assertion results with the actual frontend/backend source versions, dirty patch and untracked files, SDK versions, container images/mounts, and exact coverage limits. Container labels or a commit SHA alone do not identify a dirty mounted source tree.

Keep credentials and authenticated browser state outside Git and published artifacts. Scan retained evidence for the actual temporary secret values. At completion, clean up resources and identities created for the task while preserving pre-existing services and data; deleting a local credential copy does not revoke its upstream key.

## Source Ownership

| Concern | Source |
| --- | --- |
| Locale resources and shared table labels | [locales](../vfe/src/i18n/locales/), [labels.ts](../vfe/src/components/traces/shared/labels.ts) |
| Model operation and model-name resolution | [modelSpanPresentation.ts](../vfe/src/components/traces/shared/modelSpanPresentation.ts) |
| User-input evidence and summary | [traceUserInput.ts](../vfe/src/components/traces/shared/traceUserInput.ts), [TraceUserInputSummary.tsx](../vfe/src/components/traces/shared/TraceUserInputSummary.tsx) |
| Shared trace metadata styling | [TraceBadge.tsx](../vfe/src/components/traces-otel/shared/TraceBadge.tsx) |
| Native trace usage summary and subtotal guard | [NativeTraceDetail.tsx](../vfe/src/components/traces-otel/NativeTraceDetail.tsx) |
| Nullable span usage and tree tooltips | [spanUsage.ts](../vfe/src/components/traces-otel/spanUsage.ts), [SpanTree.tsx](../vfe/src/components/traces-otel/SpanTree.tsx) |
| Locale-aware trace timestamps | [format.ts](../vfe/src/components/traces-otel/format.ts) |
| SDK, provider, platform, and browser acceptance | [framework E2E guide](../agent-plugins/tests/framework_e2e/README.md) |

Recheck the owning source when extending these rules. Keep run-specific receipts and test counts in validation reports rather than treating them as permanent product guarantees.
