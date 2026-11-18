# Plan: Kimi Trace Span Attributes Gap Fix

## Context

Kimi's trace span structure (C1/C2/C3/C6/C8) is complete, but several **Recommended** and **Conditionally Required** attributes are missing compared to the `docs/semconv/spans.yaml` v2.0 spec and the claudecode reference implementation. This change adds the missing attributes to achieve parity.

## Summary of New Attributes

| Span Type | New Attribute | Source |
|-----------|---------------|--------|
| C1 Root | `agentic.runtime.cwd` | payload `cwd` field |
| C1 Root | `gen_ai.usage.input_tokens` | aggregated from LLMSteps |
| C1 Root | `gen_ai.usage.output_tokens` | aggregated from LLMSteps |
| C1 Root | `gen_ai.usage.total_tokens` | sum of above |
| C2 LLM | `gen_ai.conversation.id` | session_id (threaded) |
| C2 LLM | `gen_ai.usage.total_tokens` | input + output |
| C2 LLM | `agentic.turn.index` | turn_number - 1 |
| C2 LLM | `agentic.response.index` | step_n - 1 |
| C2 LLM | `agentic.tool.count` | PreToolUse count per step |
| C3 Tool | `gen_ai.conversation.id` | session_id (threaded) |

## Changes

### File 1: `kimi/kimihooks/src/common/otel.rs` (main work)

**A. `build_llm_spans()` — add `session_id`, `turn_number` params**

Current: `pub fn build_llm_spans(records, trace_id, parent_span_id, log_cfg)`
New: `pub fn build_llm_spans(records, trace_id, parent_span_id, session_id, turn_number, log_cfg)`

Inside the function:
1. Pre-pass: scan records to build `HashMap<i64, usize>` mapping step_n → tool_count (count PreToolUse events between consecutive LLMSteps)
2. Per LLM span, add attributes:
   - `gen_ai.conversation.id` = session_id (when non-empty)
   - `gen_ai.usage.total_tokens` = input_tokens + output_tokens
   - `agentic.turn.index` = (turn_number - 1) as i64
   - `agentic.response.index` = (step_n - 1) as i64
   - `agentic.tool.count` = step_tool_counts[step_n] or 0

**B. `build_tool_spans()` — add `session_id` param**

Current: `pub fn build_tool_spans(records, trace_id, parent_span_id, log_cfg)`
New: `pub fn build_tool_spans(records, trace_id, parent_span_id, session_id, log_cfg)`

Thread session_id to `make_tool_span()`.

**C. `make_tool_span()` — add `session_id` param**

Add `gen_ai.conversation.id` = session_id (when non-empty) to attributes.

**D. `build_root_span_numbered()` — add `cwd` param, aggregate tokens**

Current: `fn build_root_span_numbered(records, trace_id, turn_number, session_id, log_cfg)`
New: `fn build_root_span_numbered(records, trace_id, turn_number, session_id, cwd, log_cfg)`

Inside:
1. In the existing LLMStep loop, accumulate total input/output tokens
2. After loop, add: `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.total_tokens`
3. Add: `agentic.runtime.cwd` = cwd (when non-empty)

**E. `build_turn_spans_numbered()` — add `cwd` param, thread all**

Current: `fn build_turn_spans_numbered(records, trace_id, turn_number, session_id, log_cfg)`
New: `fn build_turn_spans_numbered(records, trace_id, turn_number, session_id, cwd, log_cfg)`

Update internal calls:
- `build_root_span_numbered(... session_id, cwd, log_cfg)`
- `build_llm_spans(... session_id, turn_number, log_cfg)`
- `build_tool_spans(... session_id, log_cfg)`

**F. Wrapper/entry functions — thread `cwd`**

- `build_turn_spans()`: pass `cwd=""`
- `build_all_turns()`: pass `cwd=""`
- `build_all_turns_with_session()`: add `cwd` param, thread through
- `build_root_span()`: pass `cwd=""`

**G. `build_subagent_spans()` — update recursive calls**

Update internal calls to `build_llm_spans()` and `build_tool_spans()` with the new params:
- `build_llm_spans(&g.nested, trace_id, &sub_span_id, session_id, turn_number, log_cfg)`
- `build_tool_spans(&g.nested, trace_id, &sub_span_id, session_id, log_cfg)`

**H. Tests — update call signatures**

Tests calling `build_tool_spans(records, trace, "", &log_cfg)` → add `""` for session_id.
Tests calling `build_llm_spans(...)` → add `""` and `1` for session_id/turn_number.

### File 2: `kimi/kimihooks/src/hooks/stop.rs`

- `export_to_remote()` signature: add `cwd: &str` param
- Call site at line ~200: pass `&cwd` from `handle()` scope
- Inside `export_to_remote()`: pass `cwd` to `build_all_turns_with_session(records, session_id, cwd, &log_cfg)`

### File 3: `kimi/kimihooks/src/bin/kimihooks_replay.rs`

- Call at line ~264: `build_all_turns_with_session(&records, &session_id, &log_cfg)` → add `""` for cwd (replay doesn't have cwd context, empty is fine)

## Verification

1. `cd kimi/kimihooks && cargo check` — compile check
2. `cd kimi/kimihooks && cargo test` — all existing tests pass
3. Grep: `grep -n "agentic.runtime.cwd\|agentic.turn.index\|agentic.response.index\|agentic.tool.count\|gen_ai.usage.total_tokens" kimi/kimihooks/src/common/otel.rs` — confirm new attributes appear
