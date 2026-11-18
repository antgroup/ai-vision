# AI Agent Observability Development Guide

This document defines the development workflow, quality constraints, and acceptance criteria for AI Agent observability.

## Core Principles

1. **Separate runtime sources of truth from replay inputs** — Build live observability from host hook/runtime payloads. Reconstruct offline replay from host-managed transcripts for binary integrations or raw callback envelopes recorded by developer replay plugins for in-process integrations. The two paths share only the main semantic construction pipeline.
2. **weaver-guard is the acceptance gate** — Replay must pass weaver-guard validation to qualify.
3. **Semantic conventions are mandatory** — Required attributes defined in `docs/semconv/` must not be omitted or renamed.
4. **Capability specifications define the semantic requirements** — Semconv and Rego constraints derive from `docs/capabilities/` (A1 / B1-B6 / C1-C7 / D1-D4 / E1-E4, 22 capabilities in total).
5. **The Makefile is the standard operating interface** — Expose every operation through a Makefile target.

## Agent Capability Maturity

**Seven telemetry plugins** are included in the cross-Agent `CAPABILITIES.md` matrix. Each plugin has 21 individual evidence files: `<agent>/capabilities/A1.md` through `D4.md`, plus `E1.md`, `E2.md`, and `E3.md`.

| Agent | Current level/status | Rating summary |
|-------|----------------------|----------------|
| claudecode | L5 Automated | 15 ✅ / 4 🟡 / 1 ❌ / 1 ⚪ |
| codex | L5 Automated | 15 ✅ / 3 🟡 / 1 ❌ / 2 ⚪ |
| kimi | Assessed | 16 ✅ / 0 🟡 / 2 ❌ / 3 ⚪ |
| openclaw | L5 Automated | 12 ✅ / 5 🟡 / 1 ❌ / 3 ⚪ |
| opencode | L5 Automated | 12 ✅ / 8 🟡 / 0 ❌ / 1 ⚪ |
| pi | candidate baseline | 12 ✅ / 3 🟡 / 2 ❌ / 4 ⚪ |
| hermes | L5 Automated | 11 ✅ / 4 🟡 / 1 ❌ / 5 ⚪ |

> See each Agent's `<agent>/CAPABILITIES.md` for aggregate explanations. Individual code, replay, and source evidence lives in `<agent>/capabilities/<ID>.md`, including E1 Git repository context, E2 upstream TRACEPARENT propagation, and E3 attribution of non-human or externally triggered activity. See [CONSTITUTION §3.7](CONSTITUTION.md) for the L0-L3 maturity ladder.

**🚧 In progress or excluded from the cross-Agent matrix:**

| Agent | Status |
|-------|--------|
| dsh | In progress — custom capture, traces/metrics/events, and offline replay are implemented; both forms of weaver-guard evidence still need to be collected |

## Development Workflow

### Phase 0: Product Boundaries and Configuration

Classify the implementation before defining its configuration. Do not apply a single set of `SESSION_DIR / REPLAY_DIR / DEBUG` settings to every component.

| Family | Production artifact for users | Replay artifact for developers only | Replay input | Configuration boundary |
|--------|-------------------------------|-------------------------------------|--------------|------------------------|
| External binary | Hook binaries such as `cchooks` / `cdxhooks` | A `<tool>-replay` binary package built, released, and installed separately | Host-managed transcript | Production binaries do not copy transcripts or define a SESSION_DIR contract; the replay CLI parses its own input/output arguments |
| In-process plugin | `observ-openclaw` / `observ-opencode` / `observ-pi` | `openclaw-replay` / `opencode-replay` / `pireplay` | Versioned raw callback envelopes recorded by the replay plugin | Packages, installation, registration, startup/shutdown, configuration files, environment variables, and uninstallation are fully independent |
| In-process plugin: dsh exception | `observ-dsh` | `dsh-replay` | Host-managed dsh canonical session JSONL; no capture envelope is needed | The production cordis bundle and replay CLI are independent; production only reads the `session/*`/`agent/*` bus and does not write transcripts |

The following configuration belongs exclusively to replay plugins. Production plugins must not read or proxy these settings:

| Replay plugin | Explicit opt-in | Transcript directory | Debug |
|---------------|-----------------|----------------------|-------|
| openclaw-replay | `OPENCLAW_REPLAY_CAPTURE_ENABLED=true` / `captureEnabled: true` | `OPENCLAW_REPLAY_SESSION_DIR` | `OPENCLAW_REPLAY_DEBUG` |
| opencode-replay | `OPENCODE_REPLAY_CAPTURE_ENABLED=true` / `captureEnabled: true` | `OPENCODE_REPLAY_SESSION_DIR` | `OPENCODE_REPLAY_DEBUG` |
| pireplay | `PIREPLAY_CAPTURE_ENABLED=true` / `captureEnabled: true` | `PIREPLAY_SESSION_DIR` | Replay-specific configuration |

Constraints:

- Production `enableTelemetry`, production `_DEBUG`, and production exporter settings must never enable replay capture.
- Do not create transcript files or directories when replay is not installed or has not been explicitly enabled.
- Replay may write local raw transcripts and explicit test/verification files. Its only permitted network destination is `http://127.0.0.1:4319`, optionally followed by a standard OTLP signal path. It must not accept or inherit remote endpoints or credentials.
- `AIVISION_HOME` defaults to `~/.aivision` and provides only the **developer artifact root after explicit opt-in**. Its presence does not authorize production or replay plugins to write files.
- Apply private permissions only to directories and files newly created by replay. Leave existing directory permissions unchanged, and initialize directories at most once per writer/session.
- Some binary integrations still copy SESSION_DIR content or bundle a replay binary with production. Treat these as legacy implementations awaiting migration, not contracts to copy for new integrations.

| Component | Transcript | Replay | Golden |
|-----------|------------|--------|--------|
| claudecode | `~/.aivision/cc/transcripts` | `~/.aivision/cc/replays` | `~/.aivision/cc/golden` |
| codex | `~/.aivision/cdx/transcripts` | `~/.aivision/cdx/replays` | `~/.aivision/cdx/golden` |
| openclaw | `~/.aivision/ocw/transcripts` | `~/.aivision/ocw/replays` | `~/.aivision/ocw/golden` |
| opencode | `~/.aivision/ocd/transcripts` | `~/.aivision/ocd/replays` | `~/.aivision/ocd/golden` |
| pi | `~/.aivision/pi/transcripts` | `~/.aivision/pi/replays` | `~/.aivision/pi/golden` |
| kimi | `~/.aivision/kimi/transcripts` | `~/.aivision/kimi/replays` | `~/.aivision/kimi/golden` |
| hermes | `~/.aivision/hermes/transcripts` | `~/.aivision/hermes/replays` | `~/.aivision/hermes/golden` |
| dsh | `~/.aivision/dsh/transcripts` (read-only discovery of host sessions) | `~/.aivision/dsh/replays` | `~/.aivision/dsh/golden` |

These directories contain runtime artifacts explicitly generated by developers; do not commit them to Git. Put inputs that need version control in the component's `tests/fixtures/`. Production DEBUG may retain each plugin's existing diagnostic/file behavior, but it must not load a replay plugin, record raw callback transcripts, or change production HTTP reporting.

### Phase 1: Data Collection — Live Production and Developer Replay

| Path | Source of truth | Persistence responsibility |
|------|-----------------|----------------------------|
| Live production collection | Host hook payload, runtime context, and usage/tool/model data exposed by the host | Production plugins do not persist callback transcripts for replay; binary hooks do not copy host transcripts |
| Offline binary replay | Transcripts already maintained by the host | The independent replay binary reads host files directly |
| Offline plugin replay | Raw callback envelopes captured by the developer replay plugin | Only the replay plugin writes JSONL, after explicit opt-in |

Record plugin raw callback envelopes before normalization. Include at least the schema version, Agent/plugin identity, hook name, timestamp, available session/correlation ID, raw input, raw context, the output/result/error actually provided by the API, and redaction/integrity metadata. `raw` means semantically unnormalized; it does not permit storing secrets such as tokens or credentials. Redact sensitive fields at the file-writing boundary.

Transcript names remain `session_{context}_{hash}.jsonl`. Production and replay share the semantic engine/normalizer, but not configuration, enabled state, exporters, or lifecycle.

### Phase 2: OTLP Reconstruction — Live and Offline Replay

The two execution modes **must share the same main span-construction pipeline** (CONSTITUTION 1.6):

| Mode | Trigger | Implementation | Availability |
|------|---------|----------------|--------------|
| **Live** production in-process plugin or hook binary | Host hook invocation | Construct spans directly and use the production exporter; independent of replay installation/configuration | All Agents |
| **Offline replay** | Independent replay CLI reads host transcripts or raw callback JSONL | Normalize offline, then reuse the production semantic engine to construct OTel data; network output is restricted to `http://127.0.0.1:4319` | Independent replay artifacts |

Explicit verification may write OTLP `ExportTraceServiceRequest` JSONL named `spans_{context}_{hash}.jsonl`. File output must not implicitly trigger HTTP requests. Any replay HTTP output must go only to the fixed local collector.

### Phase 3: Validation and Iteration

```
make replay-all <agent> → make validate <agent> → read reports → update code → repeat
```

| Level | Meaning | Requirement |
|-------|---------|-------------|
| VIOLATION | Mandatory rule violated | Must fix |
| IMPROVEMENT | Recommended attribute missing | Should fix |
| SEMCONV | Attribute absent from the registry | Register or remove |

### Phase 4: Makefile Integration

The eight standard targets, aligned with METHODOLOGY §3.3, are `build` / `replay` / `replay-all` / `validate` / `validate-one` / `report` / `report-shift` / `clean`.

## Test and Release Entry Points

Run commands from this directory. Use `make help` to list Agent names and `make help <agent>` to list the selected Agent's targets. See the Agent's documentation for prerequisites.

- For a configured build, run `make test <agent>`. For offline regression, prepare the documented inputs, then run `make replay-all <agent>` and `make validate <agent>`. These targets may rebuild the default edition and require its build-time configuration.
- For a clean checkout, use the Agent's documented common-edition test and replay commands where available. For example, [Claude Code replay](claudecode/cchooks/README.md#replay-工具) can produce OTLP JSONL for [direct Weaver validation](METHODOLOGY.md#14-validate合规校验).
- For local packaging, see the Agent's `release` target and the [AI Vision plugin build instructions](README.md#部署者准备). Follow the [deployment guidelines](../../deploy/AGENTS.md) and [release contract](../../deploy/native-packaging/release-contract.md) for product release and publication work.

## Observability Capability Specifications and Agent Assessment

> **The three primary documents:** [CAPABILITIES.md](CAPABILITIES.md), [CONSTITUTION.md](CONSTITUTION.md), and [METHODOLOGY.md](METHODOLOGY.md). Their introductions explain the division of responsibility.
>
> This file (`AGENTS.md`) is the Agent project's **development entry point and checklist overview**. Refer each specific action to the relevant section of those three documents. See [METHODOLOGY §1](METHODOLOGY.md) for the Phase 0-4 workflow details.

### Capability Specification Structure

The specifications in `docs/capabilities/` define 22 capabilities in five groups: A1 / B1-B6 / C1-C7 / D1-D4 / E1-E4. Each Agent included in the matrix has aggregate ratings in `<agent>/CAPABILITIES.md`. Individual `<agent>/capabilities/<ID>.md` files record the four-state rating, code `file:line` references, and replay/source evidence for each capability.

The four rating states are:

- **✅ Complete:** all required attributes are present and replay fields are correct.
- **🟡 Partial:** implemented, but attributes are missing, topology is incorrect, or only some scenarios are covered.
- **❌ Not implemented:** the source **does provide** the data, but the implementation does not collect it; this is an engineering gap that can be fixed.
- **⚪ Not applicable:** the source **cannot provide** the data; the implementation cannot fill this gap.

### Agent Scorecards

> Each of the seven assessed Agents maintains ratings and evidence for 21 capabilities. E4 is specified and awaits assessment.
>
> - Aggregate self-assessment: `<agent>/CAPABILITIES.md`
> - Individual capability evidence: `<agent>/capabilities/<ID>.md`
> - Project-wide matrix and shared gaps: [CAPABILITIES.md](CAPABILITIES.md)
> - Assessment methodology, including both forms of evidence and detailed four-state criteria: [CONSTITUTION.md §4](CONSTITUTION.md)
>
> Documentation checks: run `python3 scripts/validate_capabilities.py` to check the evidence files, code references, aggregate ratings, summary counts, and matrix links.

### Capability Documentation Index

> Each Agent's `CAPABILITIES.md` is an aggregate page. Individual ratings and collection evidence live in `<agent>/capabilities/<ID>.md`, for example `claudecode/capabilities/C7.md`.

| Document | Path |
|----------|------|
| Capability overview and cross-Agent matrix | `CAPABILITIES.md` |
| Engineering methodology: six workflows, data model, and integration | `METHODOLOGY.md` |
| Group A (A1) | `docs/capabilities/A-session-topology.md` |
| Group B (B1-B6) | `docs/capabilities/B-tool-semantics.md` |
| Group C (C1-C7) | `docs/capabilities/C-llm-semantics.md` |
| Group D (D1-D4) | `docs/capabilities/D-advanced-semantics.md` |
| Group E (E1-E4) | `docs/capabilities/E-repository-context.md`, `docs/capabilities/E-upstream-trace-context.md`, `docs/capabilities/E-meta-trigger.md`, `docs/capabilities/E-session-privacy.md`; VCS, propagation, trigger attribution, and privacy fields are described in `docs/semconv/vcs.md` |
| claudecode assessment/evidence | `claudecode/CAPABILITIES.md` / `claudecode/capabilities/` |
| codex assessment/evidence | `codex/CAPABILITIES.md` / `codex/capabilities/` |
| kimi assessment/evidence | `kimi/CAPABILITIES.md` / `kimi/capabilities/` |
| openclaw assessment/evidence | `openclaw/CAPABILITIES.md` / `openclaw/capabilities/` |
| opencode assessment/evidence | `opencode/CAPABILITIES.md` / `opencode/capabilities/` |
| pi assessment/evidence | `pi/CAPABILITIES.md` / `pi/capabilities/` |
| hermes assessment/evidence | `hermes/CAPABILITIES.md` / `hermes/capabilities/` |
| Engineering principles and governance | `CONSTITUTION.md` |

## AI-Assisted Iteration Protocol

With the selected Agent's build and input prerequisites in place, repeat up to `max_iterations`:

```text
    1. make build <agent>
    2. make replay-all <agent>
    3. confirm that replay generated the expected files
    4. run make validate <agent> and stop when the replay validation passes
    5. analyze violations and improvements
    6. fix similar issues together
```

For a machine-readable report, run `weaver-guard validate` directly with the relevant policy and semantic convention directories and `--format json`. Make also prints build and replay progress to stdout.

**Remediation guidance:**

- `missing_required_attribute` → find the corresponding field mapping in the transcript.
- `invalid_source_value` → confirm that the source is in the allowlist.
- `disallowed_attr_prefix` → check the attribute name prefix.
- `negative_token_count` → check token extraction logic.
- `unknown_attribute` → register it in semconv or remove it from replay.

**Constraints:** do not weaken semconv requirements, hardcode fabricated data, or skip validation.

## File Conventions

- Transcript: `~/.aivision/<component>/transcripts/session_{context}_{hash}.jsonl`
- Replay: `~/.aivision/<component>/replays/spans_{context}_{hash}.jsonl`
- Golden: `~/.aivision/<component>/golden/`
- Agent Semconv: `docs/semconv-<agent>/` (optional; detected automatically by the Makefile)

## Three Runtime Models

| Feature | Model A: external binary (Rust) | Model B: in-process plugin (TypeScript) | Model C: in-process plugin (Python) |
|---------|--------------------------------|----------------------------------------|------------------------------------|
| Agent | claudecode, codex | openclaw, opencode | hermes |
| Architecture | Agent forks a hook binary | Agent invokes the plugin API in-process | Hermes observer hooks → canonical pipeline |
| State | Stateless (stdin JSON) | Stateful (in-memory span tree) | Stateful (in-memory span tree + hook JSONL) |
| Dual output | `write_trace_jsonl_if_debug()` | `TracerProvider` registers two `SpanProcessor` instances | HTTP OTLP exporter + semantic file exporter |
| Examples | cchooks, cdxhooks | observ-openclaw, observ-opencode | observ-hermes |

## Hook Code Organization

Split files by responsibility; do not let a single file accumulate all behavior:

| Responsibility | Rust reference | TypeScript reference |
|----------------|----------------|----------------------|
| Event handling | `common/hook.rs` | `session.ts`, `tool.ts` |
| State management | `hooks/langfuse/state.rs` | `state.ts` |
| OTel construction | `common/otel.rs` | `telemetry.ts` |
| Reporting | `hooks/langfuse/langfuse_handler.rs` | `index.ts` |
| Message formatting | `common/message_format.rs` | — |

## Reference Implementations

| Component | Path | Notes |
|-----------|------|-------|
| Hook program | `claudecode/cchooks/` | Rust, HookHandler trait |
| Legacy transcript copying, awaiting migration | [Langfuse handler](claudecode/cchooks/src/hooks/langfuse/langfuse_handler.rs) | Production binaries must not own SESSION_DIR |
| Environment variables | [OTel construction](claudecode/cchooks/src/common/otel.rs) | REPLAY_DIR |
| Legacy replay layout, awaiting migration | [Replay CLI](claudecode/cchooks/src/bin/cchooks-replay.rs) | Reuse the semantic pipeline when moving replay into an independent package |
| Makefile | `claudecode/Makefile` | Standard target template |
| Semantic conventions | `docs/semconv/` | YAML definitions |
| Validation policies | `weaver/policies/` | Rego rules |
| Validation engine | `weaver/weaver-guard/` | Rust CLI |

## Acceptance Criteria

```
[MUST] Production collectors and replay recorders are independent artifacts with separate installation, configuration, startup/shutdown, and uninstallation
[MUST] Production enableTelemetry/DEBUG/exporter settings do not enable replay capture or create replay transcripts
[MUST] Production binary hooks do not copy host transcripts; replay is a separately released and installed binary package
[MUST] Plugin replay records versioned raw callback envelopes only after explicit opt-in; normalization happens offline
[MUST] Replay's network destination is fixed at http://127.0.0.1:4319; it does not read remote production endpoints or credentials
[MUST] Existing OpenCode/OpenClaw/Pi production hook-to-OTel behavior, HTTP exporters, configuration, and packaging do not regress
[MUST] Existing directory permissions remain unchanged; new replay files are private; mkdir/chmod is not repeated for each record
[MUST] weaver-guard validate reports zero violations
[MUST] All eight standard Makefile targets work
[MUST] Transcript and replay files follow the naming conventions
[MUST] Span hierarchy is correct: generation and tool spans are siblings under turn
[MUST] All required attributes are present and valid
[MUST] Messages use the OpenAI-compatible JSON array format
[MUST] Token counts correspond to individual LLM calls and are not reported again at turn level
[SHOULD] Keep the number of improvements as low as possible
[SHOULD] Have no unknown_attribute semconv issues
[SHOULD] Mark truncation in metadata and missing data with a missing marker
[OPTIONAL] Agent-specific semconv extension layer
[OPTIONAL] Capability assessment reports cover all 22 capabilities: A1 / B1-B6 / C1-C7 / D1-D4 / E1-E4
```
