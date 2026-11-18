# OpenClaw Agent Exploration Report

## 1. Directory Structure

```
ai-vision/agent-plugins/openclaw/
├── observ-openclaw/          (Main TypeScript plugin - 0.3.1)
│   ├── src/
│   │   ├── index.ts          (Plugin entry point)
│   │   ├── telemetry.ts      (OTel initialization: tracer, meter, logger)
│   │   ├── hooks/
│   │   │   ├── index.ts      (Hook registration hub)
│   │   │   ├── session.ts    (before_model_resolve, before_prompt_build, before_agent_start, agent_end)
│   │   │   ├── llm.ts        (llm_input, llm_output)
│   │   │   ├── tool.ts       (before_tool_call, after_tool_call, tool_result_persist)
│   │   │   ├── state.ts      (Global registries: sessionMap, turnMap, runRegistry)
│   │   │   └── types.ts      (TypeScript interfaces)
│   │   ├── config.ts         (Configuration parsing)
│   │   ├── semconv.ts        (Semantic convention constants & span helpers)
│   │   ├── replay.ts         (Replay CLI: transcript JSONL → OTLP JSONL)
│   │   ├── trace-bridge.ts   (HTTP trace interception)
│   │   └── sofa-otel.ts      (SOFA trace propagation)
│   ├── package.json          (TypeScript, OTel 0.214.0+, esbuild)
│   ├── CONFIG.md
│   ├── EVENTS.md
│   ├── design.md             (Hook lifecycle & span hierarchy)
│   └── dist/                 (Compiled JS output)
├── replay/                   (Alternative replay tool)
├── replays/                  (Output directory for replay spans)
├── golden/                   (Test data: spans_hooks.jsonl)
└── Makefile                  (Build orchestration)
```

## 2. Language & Tech Stack

**Language:** TypeScript (3.22+)
- Strict mode enabled
- No optional chaining abuse
- ESM modules (`"type": "module"`)

**OTel Stack:**
- `@opentelemetry/api` 1.9.0 (tracer, meter, logger)
- `@opentelemetry/sdk-trace-node` 2.6.1 (NodeTracerProvider)
- `@opentelemetry/exporter-trace-otlp-http` 0.214.0 (OTLP HTTP export)
- `@opentelemetry/sdk-metrics` 2.6.1
- `@opentelemetry/sdk-logs` 0.214.0
- Batch processors (batching + timeout optimization)

**Build:**
- TypeScript 5.7.0 compilation
- esbuild bundling
- npm package exports (default + named)

## 3. OTel Span Construction Code

**Initialization:** `src/telemetry.ts`
- Line 71-295: `initObservabilityTelemetry()` - Primary initialization
  - Creates `NodeTracerProvider` with `BatchSpanProcessor`
  - Supports dual-write with `FileSpanExporter` when debug=true
  - Resource attributes with Ant identity (username, agentType, agentProduct, agentInstance)
  - Lazy singleton pattern: `sharedTelemetry` initialized once in `register()`

- Line 308-360: `initReplayTelemetry()` - File-only replay mode
  - `FileSpanExporter` for JSONL output
  - Optional `FileLogExporter` for events

**Span Creation Helpers:** `src/semconv.ts`
- `startSpanAntAgentSpan()` - Creates AGENT spans (SpanKind.CLIENT)
- `startSpanAntChatSpan()` - Creates root CHAT spans (SpanKind.SERVER)
- `startSpanAntLlmSpan()` - Creates LLM spans
- `startSpanAntToolSpan()` - Creates TOOL spans

**Hook Wiring:** `src/hooks/index.ts` Line 55-331
- `createHooks(api, telemetry, config)` - Central registration point
- Registers 9 event handlers via `api.on()`
- Supports replay mode with mock tracer (console output only)
- Per-event handlers live in `session.ts`, `llm.ts`, `tool.ts`

**Span Tree Structure:**
```
Request Span (openclaw.chat) — requestSpan
  └── Agent Span (agent.{agentId}) — agentSpan
        ├── LLM Span (chat {model}) — llmRun.span
        │     └── Tool Span ONLY IF parentRunKey set
        └── Tool Span (tool {toolName}) — toolRun.span
              (parent: agentContext if no LLM parent)
```

## 4. Hooks Registry

**Location:** `src/hooks/index.ts` Line 214-222

| Hook | Handler | File | Priority |
|------|---------|------|----------|
| `before_model_resolve` | `handleBeforeModelResolve` | session.ts | 90 |
| `before_prompt_build` | `handleBeforePromptBuild` | session.ts | 90 |
| `before_agent_start` | `handleBeforeAgentStart` | session.ts | 90 |
| `llm_input` | `handleLlmInput` | llm.ts | default |
| `llm_output` | `handleLlmOutput` | llm.ts | default |
| `before_tool_call` | `handleBeforeToolCall` | tool.ts | default |
| `after_tool_call` | `handleAfterToolCall` | tool.ts | default |
| `tool_result_persist` | `handleToolResultPersist` | tool.ts | default |
| `agent_end` | `handleAgentEnd` | session.ts | default |

**Additional Hooks:** Command hooks (command:new, command:reset, command:stop) registered via `registerHook()` at Line 227-261

## 5. Replay Capability

**Yes — Two implementations:**

1. **`src/replay.ts`** (recommended)
   - CLI: `npx tsx src/replay.ts <transcript.jsonl> --otel <output.jsonl>`
   - Reuses `createHooks()` with mock API
   - Generates dual output: `spans_xxx.jsonl` + `events_xxx.jsonl`
   - Processes 25 hook types (skips non-handler hooks)
   - Full logging enabled by default

2. **`replay/` directory**
   - Alternative standalone replay tool
   - Similar JSONL-based workflow

**Replay Mode Flag:** `OBSERV_OPENCLAW_REPLAY=true` (Line 70, hooks/index.ts)
- Substitutes real tracer with console-only mock
- Prints all spans/events to stdout

## 6. Golden Rules Compliance Assessment

### Rule 1: Error Handling (✅ PARTIAL)
**Status:** Implemented but needs dual-channel marking
- **Span errors:** Lines in tool.ts (267), llm.ts (332-334)
  - Sets `error.type`, `error.message` attributes
  - Calls `span.setStatus({ code: SpanStatusCode.ERROR })`
- **Event errors:** llm.ts Line 336-360 emits `api_error` event when LLM fails
- **Missing:** Tool result errors NOT emitted as separate event (only span)
- **Task:** #6 Fix Rule 7: Add dual-channel error marking for tools

### Rule 2: Tool Start Time Offset (✅ COMPLIANT)
**Status:** No artificial +1ms offset detected
- Tool span starts via `startSpanAntToolSpan()` with clean context
- Duration calculated: `Date.now() - toolRun.createdAt` (Line 367, tool.ts)
- No offset injection in codebase

### Rule 3: Tool Span Hierarchy (⚠️ PARTIAL)
**Status:** Inconsistent parent selection
- **Current Logic (Lines 52-69, tool.ts):**
  - PREFERS LLM parent if `runId` matches active LLM run
  - FALLS BACK to agent context
  - NEVER uses request context for tools
- **Issue:** Tools should be children of AGENT, not LLM
  - Current code makes tools children of LLM when available
  - Violates hierarchical semantics
- **Task:** #4 Fix Rule 3: Tool spans hierarchy (children of root/agent, not LLM)

### Rule 4: Token Usage Recording (✅ COMPLIANT)
**Status:** Fully implemented
- LLM spans: `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` (llm.ts 303-312)
- Cache tokens: `gen_ai.usage.cache_read.input_tokens`, cache_creation (llm.ts 309-312)
- Metrics: Token counters in `agent_end` hook (session.ts 358-361)
- Fall back token aggregation from messages (session.ts 308-362)

### Rule 5: Tool Duration_ms (✅ COMPLIANT)
**Status:** Implemented
- **Location:** tool.ts Line 368
- Attribute: `agentic.tool.duration_ms`
- Source: Framework duration or calculated (`Date.now() - toolRun.createdAt`)
- Also emitted in tool_result event (tool.ts Line 385)
- **Task:** #7 lists as pending but code already has this (verify test coverage)

### Rule 6: LLM Duration_ms (✅ COMPLIANT)
**Status:** Implemented
- **Location:** llm.ts Line 329
- Attribute: `agentic.llm.duration_ms`
- Calculated: `Date.now() - llmRun.createdAt`
- Also in api_request event (llm.ts Line 429)

### Rule 7: Error Detection & Dual-Channel (⚠️ INCOMPLETE)
**Status:** Spans only, missing events
- **Span errors:** ✅ Present (tool.ts 267, llm.ts 332)
- **Event errors:** ⚠️ Only for LLM (llm.ts 337), NOT for tools
- **Missing:** Tool error events (`tool_error` event type)
- **Task:** #6 Fix Rule 7: Add error detection and dual-channel error marking

### Rule 8: Truncation Handling (⚠️ INCOMPLETE)
**Status:** Truncation applied but metadata NOT preserved
- **Current:** Hard-coded `.slice(0, 10000)` (llm.ts 85, tool.ts 103)
- **Issue:** No `_truncated` or `_truncation_count` metadata on attributes
- **Config:** `config.log.spans.userPrompts` etc. but no truncation flag
- **Task:** #8 Fix Rule 12: Preserve truncation metadata on span attributes

### Rule 9: Parallel Tool Detection (⚠️ INCOMPLETE)
**Status:** Detection implemented, no `parallel_group` attribute
- **Detection Logic:** state.ts Lines 145-188
  - `isParallelToolSession()` checks timing overlap + session hierarchy
  - 30s timeout window, main session tracking
- **Missing:** NO `agentic.parallel_group` attribute set on spans
- **Gap:** Detection exists but not used in span creation
- **Task:** #9 Fix Rule 13: Add parallel tool detection and parallel_group

### Rule 10: Session Lifecycle (✅ COMPLIANT)
**Status:** Fully tracked
- Session created in `before_model_resolve` (session.ts Line 55-66)
- Turn counter, active turn tracking
- Metrics: `agent.session.count` emitted on session creation

### Rule 11: Span Links (✅ COMPLIANT)
**Status:** SOFA trace propagation implemented
- Span links from HTTP headers (sofa-otel.ts)
- Built via `buildSofaSpanLink()` with role `SPAN_LINK_ROLE_AS_CHILD`
- Applied to request span (session.ts Line 101)

### Rule 12: Truncation Metadata (⚠️ INCOMPLETE)
**Status:** Truncation applied but NO metadata
- Prompts/outputs truncated to 10,000 chars
- NO `_truncated: true` or `_truncation_count` attributes
- **Task:** #8 Fix Rule 12: Preserve truncation metadata on span attributes

### Rule 13: Parallel Tool Detection & Attributes (⚠️ INCOMPLETE)
**Status:** Detection only, no span attributes
- Detection: state.ts `isParallelToolSession()` ✅
- Skipping: Parallel tool sessions skip turn creation (session.ts Line 46-50) ✅
- Missing: NO `agentic.parallel_group` attribute when tools execute in parallel
- **Task:** #9 Fix Rule 13: Add parallel tool detection and parallel_group

---

## Summary Table: Golden Rules Compliance

| Rule # | Name | Status | Issues | Task |
|--------|------|--------|--------|------|
| 1 | Error Handling | ⚠️ Partial | Missing tool error events | #6 |
| 2 | Tool Start Offset | ✅ OK | None | — |
| 3 | Tool Span Hierarchy | ⚠️ Partial | Tools parented to LLM not agent | #4 |
| 4 | Token Recording | ✅ OK | None | — |
| 5 | Tool Duration_ms | ✅ OK | Already implemented | #7 verify |
| 6 | LLM Duration_ms | ✅ OK | None | — |
| 7 | Error Dual-Channel | ⚠️ Partial | Tool errors only in spans | #6 |
| 8 | Truncation Handling | ⚠️ Partial | No metadata flags | #8 |
| 9 | Parallel Session Skip | ✅ OK | None | — |
| 10 | Session Lifecycle | ✅ OK | None | — |
| 11 | Span Links | ✅ OK | SOFA implemented | — |
| 12 | Truncation Metadata | ⚠️ Partial | No _truncated flag | #8 |
| 13 | Parallel Group Attr | ⚠️ Partial | Detection exists, attribute missing | #9 |

---

## Key Files for Golden Rules Fixes

| Fix # | Task | Files | Lines |
|-------|------|-------|-------|
| #4 | Tool hierarchy | tool.ts | 52-80 (parent selection) |
| #6 | Error dual-channel | tool.ts, llm.ts | 350-360 (llm), ~300 (tool) |
| #7 | Duration_ms verify | tool.ts, llm.ts | 368, 329 |
| #8 | Truncation metadata | llm.ts, tool.ts, semconv.ts | 85, 103, helpers |
| #9 | Parallel group attr | state.ts, tool.ts | 169-188 (detection), 71-80 (span creation) |

