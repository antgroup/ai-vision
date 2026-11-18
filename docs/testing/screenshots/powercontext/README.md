# Native PowerContext UI evidence

Actual browser captures of the tested source identified by `source-manifest.json`, using its paired SigNoz data-plane commit. The same locally stored 24-span trace was produced by official `FakeMessagesListChatModel` and the actual PowerContext HTTP SDK / ASGI / SQLite runtime. Content and account values belong to the local fixture.

- [Trace business summary](native-trace-overview.png): two major calls, preparation and completed-turn capture. Binding, HTTP wrappers and internal stages remain in the complete left chain without becoming extra summary calls.
- [MCP memory tool comparison](mcp-memory-tools.png): the same PowerContext engine retains tool vocabulary for explicit MCP calls; this stored regression fixture is synthetic.
- [Standalone memory overview](native-memory-overview.png): the same two business calls and one executed, successful search within the selected window.
- [Standalone operation statistics](native-memory-tools.png): the same localized names and measured SDK durations.
- [Standalone recent calls](native-memory-calls.png): original operation titles and source IDs remain available; preview limits do not change aggregates.
- [Middleware recall Run](native-recall-run.png): actual input, recalled historical memory, citation and evidence navigation.
- [Capture SDK Run](native-capture-run.png): source navigation selects the client business boundary; no server body is required.

Expanded Trace call records follow the neutral MCP/Skill card and source-link style. Both views retain 13.423/7.663 ms from the same SDK spans; Trace sums these measurements while the standalone page reports window percentiles.

The live isolated Memory API returned two calls, one executed search and one hit, with inventory still unknown. Browser assertions verified both summary records navigate to their actual SDK spans (`e7cd1c686d6e0185`, `f79507edb2e78e02`), and all 24 left-chain records remain. No telemetry was rewritten or re-ingested. The model response is preset; these images do not establish inference quality or deployment of a release.

Standalone native pages use “operations” for the count, tab, table and measurement help; MCP tools retain their existing vocabulary and configuration catalog. Mixed windows name both. Full-window `callKinds` metadata preserves this distinction when older calls are outside the recent preview. Per-image source commits distinguish the preserved earlier Trace/Run captures from the refreshed standalone views.
