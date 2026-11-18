# observ-hermes 0.2.0 Release Checklist

Release target:

- Plugin: `observ-hermes 0.2.0`
- Runtime: Hermes Agent `v0.18.2`
- Observer schema: `hermes.observer.v1`
- Validation authority: repository `docs/semconv/` plus `weaver-guard` v4.x

## Required Gates

- [x] Hook contract pinned to Hermes `0.18.2`.
  - Evidence: `hermes/tests/fixtures/hooks/v0.18.2/observer_envelopes.json`
  - Regenerator: `python3 scripts/capture_hermes_v0182_hooks.py`
- [x] Production and replay share only `aivision-hermes-core==0.2.0` semantic
  construction.
  - Evidence: `hermes-core/`, `observ_hermes/pipeline.py`,
    `hermes-replay/hermes_replay/replay.py`
- [x] Production owns no replay capture, transcript writer, replay file sink, or
  replay/report console entry point.
- [x] Replay is an independently installed, disabled-by-default product that
  records `hermes.replay.raw.v1` envelopes before normalization.
- [x] Replay report accepts only `http://127.0.0.1:4319/v1/{traces,metrics,logs}`
  and exposes no credential/header override.
- [x] Production and replay schemas are strict and separate at
  `$HERMES_HOME/observ-hermes.json` and `$HERMES_HOME/hermes-replay.json`.
- [x] Full/privacy, JSON/protobuf, signal enablement, and delta/cumulative
  temporality paths have serialized-boundary regression coverage.
- [x] Atomic install keeps rollback state outside Hermes plugin scan.
  - Install path: `~/.hermes/plugins/observ_hermes`
  - Backup path: `~/.hermes/plugin-backups/observ_hermes.previous`
  - Rollback command: `make rollback`
- [x] Tracked edition configs contain no bundled authorization headers,
  tokens, API keys, or passwords.
- [x] Runtime OTLP header injection remains supported.
  - Use `OTEL_EXPORTER_OTLP_HEADERS` or per-signal
    `OTEL_EXPORTER_OTLP_{TRACES,METRICS,LOGS}_HEADERS`.
- [x] Header values are excluded from configuration representations.
- [x] Regression tests reject credential-bearing edition configs.
- [x] `make install hms` installs only production; `make install hms-replay`
  installs replay and its three commands; compatible version changes use
  `make install hms-cohort`.
- [x] Post-split full/privacy replay, weaver, golden, and leak-scan evidence
  regenerated for this release candidate.
  - Replay, each mode: 3 files / 13 signals, 0 violations, 0 semconv issues,
    10 improvements, pass=true.
  - Live HTTP/JSON, each mode: 3 files / 14 signals, 0 violations,
    0 semconv issues, 2 improvements, pass=true.
  - Golden, each mode: 3/3 derived replay files matched.
  - Privacy and credential canary scans: 0 matches in privacy artifacts;
    credential canary: 0 matches in both modes.
  - Pinned package verification: `hermes-agent==0.18.2` fixture contract passes.
- [ ] External credential rotation verified.
  - Tracking reference: `EXTERNAL-ROTATION-REQUIRED-HERMES-2026-07-20`
  - Owner: AiVision credential owner outside this repository
  - Status: pending external verification

## Honest Skips

The latest golden evidence intentionally records these skips:

- `mcp-tool`: skipped because no configured MCP server was available.
- `skill-call`: skipped because no installed Hermes skill was available.
- `api-error`: skipped because forcing a safe isolated provider/API failure was
  not authorized and user credentials were not mutated.
- `compaction`: skipped because Hermes v0.18.2 has no documented compaction
  observer hook; heuristic inference is forbidden.

## Release Decision

Repository-side release approval requires fresh post-split replay/weaver/golden
evidence plus external credential-owner verification for
`EXTERNAL-ROTATION-REQUIRED-HERMES-2026-07-20`.

Deleting credentials from tracked files is not evidence that a previously
exposed credential was rotated. No credential value is recorded in this
checklist.
