# Piagent capability assessment

Status: implementation candidate; intentionally excluded from `MATURE_AGENTS` pending review of this first stable baseline.

Architecture: `piagent` owns real-time OTLP telemetry; `pireplay` owns canonical transcript recording + replay CLI + golden superloop. Both subscribe to the same 16 pi lifecycle anchors and share `piagent`'s `CanonicalNormalizer` + `SemanticEngine` (via `@observ/pi` file: dependency). Capabilities scored below are produced jointly — piagent emits the live spans/events/metrics, pireplay records the transcript that the replay parity check validates against.

Baseline: [`GOLDEN_BASELINE.md`](GOLDEN_BASELINE.md), generated from local-only run `golden/runs/2026-07-13T02-41-20-691Z-8902` (supersedes `2026-07-11T05-30-31-030Z-24788`). Four required scenarios are real Pi 0.80.6 executions; unavailable subagent/MCP/skill scenarios are classified from the persisted `getAllTools()` probe. The run produced 12 realtime and 12 replay files with semantic parity and `0 violations / 12 information (llm_missing_response_id, optional)`.

Validation candidate: local run `2026-07-19T05-56-00-934Z-58311` added separate command and conversational Skill scenarios. Both sealed transcripts passed exact physical event/metric cardinality and Skill topology assertions; realtime/replay parity was `35/35 spans`, `56/56 events`, and `46/46 metric points`, with `0 violations`.

Summary: **12 ✅ / 3 🟡 / 2 ❌ / 4 ⚪** (21 capabilities).

| Capability | Status | Code evidence | Replay/source evidence |
|---|:---:|---|---|
| A1 multi-turn chain topology | ✅ | `observ-pi/src/semantic.ts`: one CHAT per user turn; LLM/TOOL/COMPACTION use the CHAT key as parent | `multi-tool` has 3 TOOL spans and multiple LLM siblings; `parity.json` reports 20/20 spans equal |
| B1 tool duration | ✅ | tool start/end timestamps produce `agentic.tool.duration_ms` | `tool-success`, `tool-error`, and `multi-tool` replay TOOL spans pass Weaver |
| B2 tool error | ✅ | failed TOOL gets error status/type/message plus error metric | `tool-error` contains a real failed `read` call and passes Weaver |
| B3 tool arguments | ✅ | start hook persists allowlisted args and emits JSON `gen_ai.tool.call.arguments` | real `read`, `write`, and `bash` TOOL spans contain arguments |
| B4 tool result | ✅ | end hook emits result on span and `tool_result` event | required tool scenarios contain real success/error results |
| B5 MCP classification | ⚪ | classifier supports `mcp__`/`mcp-*` names | capability probe: `no mcp tool among 7 configured tools`; no source event exists to classify |
| B6 Skill classification | ✅ | `observ-pi/src/skill-detection.ts` recognizes Pi-compatible command expansion and a bounded, Turn-scoped `<available_skills>` catalog; `semantic.ts` emits command or linked 0ms derived Skill spans, preserves native/physical calls, and applies causal precedence when an explicit occurrence ID exists; `telemetry.ts` bounds single-use Link contexts to 256 | 73 observ-pi tests and 23 pireplay tests cover command/conversation paths, catalog invalidation/conflicts, native-vs-fallback precedence, privacy, Link correlation, Turn tags, and exact physical event/metric cardinality; pinned Pi 0.80.6/0.80.10 fixtures verify real expansion/catalog shapes. Real run `2026-07-19T05-56-00-934Z-58311` passed both sealed Skill scenarios, realtime/replay parity, and Weaver/AiVision with 0 violations |
| C1 LLM duration | ✅ | provider request to assistant message boundary produces `agentic.llm.duration_ms` | every real scenario contains independently validated LLM spans |
| C2 LLM error | 🟡 | API status/message errors emit error span/event and RED error count; unit covered | no real Golden scenario safely forces a provider API failure, so replay evidence is absent |
| C3 user input | ✅ | CHAT receives the user prompt; LLM spans use shared pre-projection materialization so `gen_ai.input.messages` is the full current snapshot for an equal retry, the appended suffix for a changed tool-loop call, the current user message at a new Turn boundary, or a truthful reset snapshot, without `agentic.input.delta` | unit replay proves equal retry snapshots, tool-loop deltas, cross-Turn final-assistant trimming, legacy-v1 full snapshots, checksum coverage, and independent recorder-normalizer parity; Golden parity remains the end-to-end gate |
| C4 system instructions | ✅ | provider request projection maps the effective system array to each LLM span/event | real replay contains structured `gen_ai.system_instructions` and passes Weaver |
| C5 model output | ✅ | assistant blocks become structured output with content/tool calls/finish reason | real basic and tool-chain replay LLM spans contain output messages |
| C6 thinking | ✅ | assistant thinking blocks map separately to `reasoning_content` | selected real model returned thinking blocks; replay preserves them separately from content |
| C7 token usage | ✅ | per-generation usage maps only to its LLM; normalized input includes cache-read and cache-write once; Pi 1.0.2 native provider chunks preserve explicit zero and missingness | 0.80.x fixtures retain positive usage but cannot prove that initialized zero was reported; native E2E pins 1.0.2 for unknown/zero/cache cases; CHAT has no token duplication |
| D1 compaction | 🟡 | before/after compaction hooks emit a sibling COMPACTION span and result event | no compaction is forced in the bounded Golden suite |
| D2 synchronous subagent | ⚪ | — | capability probe: `no subagent tool among 7 configured tools` |
| D3 asynchronous subagent | ⚪ | — | Pi configuration exposes neither a subagent tool nor a cross-trace correlation source |
| D4 cancel/retry recovery | 🟡 | API failure and session-shutdown cancellation paths close spans without leaking state; superloop retries rate-limited scenarios with partial-transcript cleanup | unit/error paths exist; bounded Golden exercises provider rate-limit retry (tool-error passed on retry 1) but not user-interrupt cancel |
| E1 Git repository context | ❌ | Pi context exposes trusted cwd, but no Git collector or VCS Resource projection exists | `pi/capabilities/E1.md`; `docs/data-use/pi/traces.md` current omission |
| E2 upstream TRACEPARENT chaining | ⚪ | Pi host hooks expose no external W3C upstream context source | `pi/capabilities/E2.md`; direct OTel parent unit test is not a host source |
| E3 non-real-user / external trigger attribution | ❌ | Explicit `AIVISION_META_TRIGGER` / `meta.trigger` and both trigger fields are not implemented | `pi/capabilities/E3.md` |

Local-only detailed evidence files (intentionally ignored because they contain real prompts/model output):

- `golden/runs/2026-07-13T02-41-20-691Z-8902/manifest.json`
- `golden/runs/2026-07-13T02-41-20-691Z-8902/parity.json`
- `golden/runs/2026-07-13T02-41-20-691Z-8902/diagnosis.json`
- `golden/runs/2026-07-13T02-41-20-691Z-8902/capabilities.json`
- `golden/runs/2026-07-13T02-41-20-691Z-8902/logs/validate.log`

The assessment follows the four-state rules in `docs/capabilities/`: 🟡 means the implementation or source coverage is incomplete; ⚪ means the current Pi runtime/configuration exposes no source event from which the capability could be truthfully constructed.
### E2 upstream TRACEPARENT chaining

- **Status**: ⚪
- **Conclusion**: See [independent evidence](capabilities/E2.md): the current Pi host contract exposes no external W3C upstream context source.

### E1 Git repository context

- **Status**: ❌
- **Conclusion**: See [independent evidence](capabilities/E1.md): Pi has a trusted `ctx.cwd` source, but the production and replay paths do not collect or emit Git remote/branch/revision.

## 单项能力证据索引

- [E1](capabilities/E1.md)
- [E2](capabilities/E2.md)
- [E3](capabilities/E3.md)
- [A1](capabilities/A1.md)
- [B1](capabilities/B1.md)
- [B2](capabilities/B2.md)
- [B3](capabilities/B3.md)
- [B4](capabilities/B4.md)
- [B5](capabilities/B5.md)
- [B6](capabilities/B6.md)
- [C1](capabilities/C1.md)
- [C2](capabilities/C2.md)
- [C3](capabilities/C3.md)
- [C4](capabilities/C4.md)
- [C5](capabilities/C5.md)
- [C6](capabilities/C6.md)
- [C7](capabilities/C7.md)
- [D1](capabilities/D1.md)
- [D2](capabilities/D2.md)
- [D3](capabilities/D3.md)
- [D4](capabilities/D4.md)
