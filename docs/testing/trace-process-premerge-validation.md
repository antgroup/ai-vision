# Execution process pre-merge review

Reviewed source: `dac5bf77ce44c01e5c4a8a52d9357f4253b2f8f1`, compared with the previous PR head `d0cd3d3fa7a802a16bfb1b3c1dfabb8029d21e28`. The following evidence-only commit adds this report. Checks ran locally on macOS on 2026-10-24; they do not establish Linux, hosted CI, release or deployment results.

## Required fixes found and verified

The execution process now uses the same reported-kind and normalization-provenance rules as the existing input/content readers. Explicit operations retain precedence; synthesized operations defer to the original SDK kind. TOOL/MCP payloads remain tools, and tool, embedding and retriever outputs cannot become invented user/assistant messages through the canonical output path. CHAIN canonical message arrays remain supported.

When no message-level tool requests exist, actual tool executions are inserted among recorded message events using their start times. Both message consumption and this final ordering share an exact nanosecond comparator. This also supports RFC3339 offsets and fractional seconds. Missing or invalid timestamps sort after known times; equal times preserve stable source order. Explicit call-ID association requirements and the separate unlinked-execution section remain intact.

Eight new regression tests were reproduced as failures before their corresponding fixes, including conflicting synthesized SDK kinds, OpenInference kinds, canonical operation payloads, standalone tools, RFC3339 timestamps and reverse-ordered events separated by 1 ns. Independent review reran the RFC3339 and precision reproductions and found no remaining required fix in the final patch.

## Verification

- Complete `npm test`: **933 passed / 26 result groups / zero failures**, plus the existing custom-script checks. The execution-process group contains 46 tests.
- `npm run lint:ci` (the entire frontend), `npm run typecheck`, `npm run build`, and `git diff --check`: passed.
- Browser checks on the current local frontend reopened representative Eino, DeepAgents, DSH and Codex records. The main process contained 11, 23, 20 and 7 entries respectively. Codex still exposed six unlinked executions and one failure separately.
- Eino search matched one of 21 Spans with three ancestors while retaining all 11 process entries. Expanding its failed result, inspecting the model source and returning preserved the open result and filter state. Filter-help pointer/keyboard/Escape and the overflow continuation were also rechecked.

## Preserved data and screenshot applicability

An offline comparison used eight previously saved real-model response records containing 271 Spans, including both the plain projection and the Native detail's canonical-output options. No new model invocation, ingestion, replay to a service, or authenticated API readback was performed by this comparison.

Seven inputs produced identical JSON projections. The remaining DeepAgents readonly input (`af0d285253877ae40bab708b8638b09b`) corrected the order of overlapping RFC3339 model events. Its 41 entries and 25 context records remain present. Context is identical; ignoring generated serial IDs, every entry's payload, original sources and tool links is preserved. A user input at `07:37:18.564792Z` now precedes the overlapping model output at `07:37:25.945037Z`. All original Span objects remained unchanged.

The four inputs used by all 18 documentation screenshots produced identical plain and canonical JSON projections:

| Runtime example | Trace ID |
| --- | --- |
| Eino repair | `11e5873130a9eb77583f9d787395e9ec` |
| DeepAgents repair | `b05210f7797ce36e6167a2d03afb17d7` |
| DSH repair | `4200554d06bb90d951c5b6dac61a57d2` |
| Codex recovery | `6a2085e78f2606269710ddfd7fb55f42` |

Those screenshots retain their actual captured source revision, `8171e536c50de0bbed96b092ee8c9a6ea7eea626`; they have not been relabeled or claimed as new captures from this fix. Their previous source/image provenance and the new sample-equivalence check establish their applicability to the reviewed examples, not identical rendering for every possible input.

The associated documentation review checked all three languages, navigation, content, image provenance and 18 unchanged native JPEGs. Five Python validators, Mintlify `validate`, `broken-links` and diff checks passed. Current hosted check status is recorded separately on the pull requests; billing-blocked jobs with zero steps are not passing CI.
