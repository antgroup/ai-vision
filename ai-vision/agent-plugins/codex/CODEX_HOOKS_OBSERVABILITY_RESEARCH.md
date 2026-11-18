# Codex Hooks Observability Research

Last updated: 2026-03-31

## Goal

Evaluate whether Codex's current hook system can provide observability similar to Claude Code's usage monitoring, especially for:

- Metrics
- Events
- Session and turn analytics
- Tool activity
- Token usage

Reference targets:

- Claude Code monitoring reference: https://code.claude.com/docs/en/monitoring-usage
- Codex hooks reference: https://developers.openai.com/codex/hooks
- Internal summary of Codex hooks: `codex/codex-hooks-overview.md`
- Current Codex implementation: `codex/cdxhooks/`

## Short Answer

Codex can support a useful observability layer, but not a full Claude Code equivalent.

What is possible today:

- Session-level events
- Prompt submission events
- Turn completion analytics
- Bash tool execution events
- Token usage analytics derived from transcript data
- Trace-style turn reconstruction after a turn ends

What is not currently available in a first-class way:

- API request events
- API error events
- Exact request-level latency metrics
- Exact cost metrics
- Full multi-tool coverage comparable to Claude Code
- Stable permission-decision telemetry across all tools

So the right framing is:

- A strong "Codex session and turn observability" system is feasible today
- A strict "Claude Code monitoring parity" implementation is not feasible today

## Official Codex Hook Surface

Based on the current Codex hooks documentation, Codex publicly exposes 5 hook events:

- `SessionStart`
- `PreToolUse`
- `PostToolUse`
- `UserPromptSubmit`
- `Stop`

Important current limitations from the official docs:

- Hooks are still experimental
- Windows support is temporarily disabled
- `PreToolUse` currently only supports `Bash`
- `PostToolUse` currently only supports `Bash`
- `UserPromptSubmit` and `Stop` do not currently honor `matcher`
- Some fields are parsed but "not supported yet", so they fail open

That means the raw hook surface is much narrower than Claude Code's monitoring surface.

## Official OTel Exposure Status

As of 2026-03-31, I did not find an official Codex feature that exports observability data as OpenTelemetry or OTLP.

What the official docs do show:

- Codex exposes lifecycle hook events such as `SessionStart`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, and `Stop`
- Codex hook output can surface messages in the UI or Codex event stream
- Codex model pages expose pricing dimensions such as input, cached input, and output tokens
- Codex model pages also expose API rate-limit dimensions such as RPM and TPM

What I did not find in the official docs:

- an OTLP exporter
- an OpenTelemetry logs endpoint
- an OpenTelemetry metrics endpoint
- an official Codex usage metrics API
- a first-class event API for request-level telemetry comparable to Claude Code monitoring

So the current official picture is:

- Codex gives us hook-triggered lifecycle events that we can consume ourselves
- Codex documents token, pricing, and rate-limit dimensions
- Codex does not appear to provide a built-in OTel telemetry pipeline

This means any OTel traces, logs, or metrics for Codex observability will need to be produced by our own hook layer, transcript parser, or backend aggregation pipeline rather than coming from Codex directly.

## What Claude Code Monitoring Exposes

Claude Code's monitoring docs include both metrics and structured events.

Examples of event types explicitly documented there:

- `claude_code.user_prompt`
- `claude_code.tool_result`
- `claude_code.api_request`
- `claude_code.api_error`
- `claude_code.tool_decision`

Examples of metrics and analyses explicitly supported there:

- token usage
- session count
- lines of code count
- commit count
- pull request count
- cost estimation
- request durations
- tool outcomes

This is broader than Codex hooks today.

## What Codex Can Reliably Capture Today

### 1. Session Lifecycle

From hooks directly:

- `SessionStart`
- `Stop`

This is enough to build:

- session count
- session start rate
- session completion count
- user/session distribution
- model distribution by session

### 2. Prompt Events

From hooks directly:

- `UserPromptSubmit`

This is enough to build:

- prompt submitted event stream
- prompt count per session
- prompt length distribution
- prompt submission rate

Depending on privacy requirements, prompt bodies may be stored or redacted.

### 3. Tool Events

From hooks directly:

- `PreToolUse`
- `PostToolUse`

But with a major limitation:

- current runtime only exposes `Bash`

This is enough to build:

- Bash invocation count
- Bash duration
- Bash success/failure rate
- Bash block/deny count
- Bash output size

This is not enough to build a full cross-tool observability system equivalent to Claude Code's `Read`/`Write`/`Edit`/`MCP` tool coverage.

### 4. Turn-Level Reconstruction

This is where Codex is stronger than the raw hook surface suggests.

The current `cdxhooks` implementation already reconstructs turns from transcript JSONL:

- `task_started`
- `task_complete`
- `user_message`
- `agent_message`
- `turn_context`
- `exec_command_begin`
- `exec_command_end`
- `token_count`

See:

- `codex/cdxhooks/src/common/transcript.rs`
- `codex/cdxhooks/src/hooks/langfuse/langfuse_handler.rs`

This means we can derive:

- turn count
- turn completion events
- turn latency
- assistant message counts
- Bash command spans inside a turn
- model and cwd per turn
- token usage per turn

### 5. Token Usage

The transcript parser already extracts:

- `input_tokens`
- `output_tokens`
- `total_tokens`
- `cached_input_tokens`
- `reasoning_output_tokens`

This is enough to support:

- token usage metrics by user
- token usage metrics by model
- token usage metrics by repo or cwd
- per-turn token histograms
- reasoning token analysis
- cache token analysis

This is one of the strongest parts of the current Codex observability story.

## What Codex Cannot Reliably Capture Today

### 1. API Request Events

Claude Code exposes first-class request events like:

- `api_request`
- `api_error`

Codex hooks do not currently expose an equivalent request-level event stream.

As a result, we cannot reliably observe:

- each individual model request
- request retry count
- request-level failure breakdown
- request-level status code
- exact request latency
- time to first token

### 2. Exact Cost Metrics

Claude Code monitoring includes estimated `cost_usd`.

Codex hooks do not currently expose a first-class cost signal.

Possible workaround:

- estimate cost offline from token counts and model pricing tables

But that would be:

- derived, not authoritative
- sensitive to pricing changes
- potentially wrong for internal routing or hidden pricing behavior

### 3. Full Tool Decision Telemetry

Claude Code documents a `tool_decision` event model with richer decision source metadata.

Codex currently allows some blocking behaviors in hooks, but:

- tool hook coverage is effectively Bash-only today
- several permission-related outputs are parsed but not fully supported
- there is no broad stable decision event surface across all tools

So Codex cannot currently match Claude's tool-governance observability.

### 4. Full Tool Catalog Observability

Because Codex hooks currently only emit Bash for `PreToolUse` and `PostToolUse`, we cannot get full first-class telemetry for:

- file read/write/edit tools
- apply patch
- MCP tool calls
- higher-level internal tool categories

Some of these may still appear indirectly in transcripts, but not with the same clean lifecycle semantics.

## Practical Capability Matrix

| Capability | Claude Code style target | Codex today |
| --- | --- | --- |
| Session events | Yes | Yes |
| Prompt events | Yes | Yes |
| Turn analytics | Yes | Yes, mostly via transcript |
| Tool result events | Yes | Partial, Bash only |
| Tool decision events | Yes | Partial, limited and not fully mature |
| API request events | Yes | No |
| API error events | Yes | No |
| Token usage metrics | Yes | Yes |
| Cost metrics | Yes | Only estimated offline |
| Request latency | Yes | Only coarse turn/tool latency |
| Multi-tool observability | Yes | No, not at Claude parity |

## Practical Event And Metric Model

The most useful implementation model is to split telemetry into 3 categories:

- events we can emit directly from hooks
- metrics we can derive from transcript or event aggregation
- fields that are currently missing or only estimable

### 1. Direct Hook Events

These can be emitted as JSONL records, OTel logs, or internal backend events with high confidence.

| Event name | Primary source | Key fields we can capture now | Confidence |
| --- | --- | --- | --- |
| `session_started` | `SessionStart` | timestamp, session id, cwd, model, user/env context | High |
| `prompt_submitted` | `UserPromptSubmit` | timestamp, session id, prompt text or redacted prompt, cwd | High |
| `bash_pre_exec` | `PreToolUse` for `Bash` | timestamp, session id, command, cwd, approval/block intent | Medium |
| `bash_post_exec` | `PostToolUse` for `Bash` | timestamp, session id, command, exit code, duration, output summary | Medium |
| `session_stopped` | `Stop` | timestamp, session id, stop reason if available, cwd | High |

Recommended note:

- treat `PreToolUse` and `PostToolUse` as Bash-only signals unless official runtime support expands

### 2. Transcript-Derived Events

These are not first-class official hook events, but they are still practical and useful if we reconstruct them after `Stop`.

| Derived event name | Primary source | Key fields we can capture now | Confidence |
| --- | --- | --- | --- |
| `turn_completed` | transcript `task_started` + `task_complete` | turn id, start/end time, model, cwd, elapsed time | High |
| `assistant_message_emitted` | transcript `agent_message` | message id, turn id, model, content length | Medium |
| `bash_span` | transcript `exec_command_begin` + `exec_command_end` | command, turn id, start/end time, exit status | High |
| `token_usage_recorded` | transcript `token_count` | input, output, total, cached input, reasoning output | High |
| `turn_context_recorded` | transcript `turn_context` | cwd, repo, branch, model, session linkage | Medium |

Recommended note:

- these are best treated as reconciliation events generated after the turn or session ends, not real-time signals

### 3. Aggregated Metrics

These should usually be produced by downstream rollups rather than emitted one-by-one from hooks.

| Metric name | How to compute | Feasibility |
| --- | --- | --- |
| `sessions_total` | count `session_started` | High |
| `sessions_completed_total` | count `session_stopped` | High |
| `prompts_total` | count `prompt_submitted` | High |
| `turns_total` | count `turn_completed` | High |
| `turn_latency_ms` | histogram from `turn_completed` elapsed time | High |
| `bash_invocations_total` | count `bash_pre_exec` or `bash_span` | High |
| `bash_failures_total` | count non-zero exit codes from `bash_post_exec` or `bash_span` | High |
| `bash_duration_ms` | histogram from Bash span duration | High |
| `tokens_input_total` | sum transcript token counts | High |
| `tokens_output_total` | sum transcript token counts | High |
| `tokens_cached_input_total` | sum transcript token counts | High |
| `tokens_reasoning_output_total` | sum transcript token counts | High |
| `token_usage_by_model` | group token counters by model | High |
| `token_usage_by_repo` | group token counters by repo or cwd | Medium |
| `estimated_cost_usd` | derive from token counts and public pricing | Medium |

### 4. Explicit Gaps

These fields should be called out as unsupported in v1 so we do not over-promise:

| Missing field or event | Why it is missing today | Suggested handling |
| --- | --- | --- |
| `api_request` | no first-class request event stream in Codex hooks | leave unsupported |
| `api_error` | no request-level error event surface | leave unsupported |
| `request_latency_ms` | no direct request timing signal | do not estimate from turn latency |
| `time_to_first_token_ms` | no streaming-phase telemetry | leave unsupported |
| `status_code` | no request/response API event payload | leave unsupported |
| `retry_count` | no request retry lifecycle signal | leave unsupported |
| full tool decision stream | current tool hooks are effectively Bash-only | support Bash only and document scope |
| authoritative cost | no official runtime cost field | expose only `estimated_cost_usd` when needed |

### 5. Recommended v1 Schema Boundary

For a first implementation, the cleanest boundary is:

- direct events: `session_started`, `prompt_submitted`, `bash_pre_exec`, `bash_post_exec`, `session_stopped`
- derived events: `turn_completed`, `bash_span`, `token_usage_recorded`
- aggregated metrics: session, prompt, turn, Bash, and token dashboards
- explicitly unsupported: request-level API telemetry and full multi-tool lifecycle telemetry

## Best Realistic Observability Model For Codex

The best design is not "copy Claude exactly".

The best design is a layered Codex-native model:

### Layer 1: Hook Events

Emit direct structured events from:

- `SessionStart`
- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `Stop`

Use this layer for:

- immediate event logging
- policy/audit events
- Bash governance
- prompt governance

### Layer 2: Transcript-Derived Turn Analytics

At `Stop`, read transcript and reconstruct:

- turns
- assistant messages
- Bash tool spans
- token usage
- model/cwd context

Use this layer for:

- turn traces
- turn-level latency
- token metrics
- productivity and usage dashboards

### Layer 3: Aggregated Metrics

From the event stream and turn reconstruction, aggregate:

- sessions per day
- prompts per day
- turns per day
- Bash count / failure rate / latency
- token usage by model
- token usage by repo
- reasoning token usage

This gives a practical internal monitoring system even without request-level telemetry.

## Recommendation

Recommendation: proceed, but scope the goal carefully.

Good target statement:

"Build a Codex observability system for session, prompt, turn, Bash, and token analytics, with optional estimated cost reporting."

Bad target statement:

"Reproduce Claude Code monitoring one-for-one."

The first is achievable with current Codex hooks and transcript data.
The second is blocked by missing request-level and multi-tool hook coverage.

## Suggested Roadmap

### Phase 1

Expand current `cdxhooks` coverage beyond `Stop`:

- add structured logging for `SessionStart`
- add structured logging for `UserPromptSubmit`
- add structured logging for `PreToolUse`
- add structured logging for `PostToolUse`

### Phase 2

Keep `Stop` as the transcript reconciliation point:

- reconstruct turn spans
- compute token deltas
- infer turn completion metrics
- persist JSONL debug/event logs locally

### Phase 3

Add metrics export or downstream aggregation:

- OTel logs
- OTel metrics
- backend-side rollups

### Phase 4

Optionally add derived cost estimation:

- model-price lookup table
- token-to-cost calculation
- explicit labeling as estimated cost

## Final Verdict

Codex hooks are sufficient for a meaningful observability platform focused on:

- sessions
- prompts
- turns
- Bash tool activity
- token usage

They are not sufficient for a true Claude Code monitoring clone because Codex currently lacks:

- first-class API request telemetry
- first-class API error telemetry
- first-class multi-tool lifecycle telemetry
- stable full-fidelity decision telemetry

So the right product decision is:

- build a Codex-native observability layer
- do not promise Claude-level parity in the first version

## Sources

- Claude Code monitoring reference: https://code.claude.com/docs/en/monitoring-usage
- Codex hooks docs: https://developers.openai.com/codex/hooks
- Codex model page (`codex-mini-latest`): https://developers.openai.com/api/docs/models/codex-mini-latest
- Codex model page (`gpt-5.2-codex`): https://developers.openai.com/api/docs/models/gpt-5.2-codex
