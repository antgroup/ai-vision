# pireplay

`pireplay` is a developer-only Pi extension and offline replay CLI. It is independent from the user-facing `observ-pi` plugin: separate package, install, `PIREPLAY_*` configuration, lifecycle, and artifacts.

The live extension records raw Pi callback input/context envelopes only. It does not create telemetry, normalize semantic events, or alter the production `observ-pi` chain. Semantic normalization and OTLP reconstruction happen offline through the shared `@observ/pi` semantic engine.

## Install and capture

```bash
make -C pi install-replay
PIREPLAY_CAPTURE_ENABLED=true pi
```

Capture is disabled by default. No callback is registered and no artifact path is resolved until `PIREPLAY_CAPTURE_ENABLED=true` is set.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PIREPLAY_CAPTURE_ENABLED` | `false` | Explicit developer capture opt-in (`1/true/yes/on`, or `0/false/no/off`) |
| `PIREPLAY_SESSION_DIR` | `~/.aivision/pi/transcripts/` when enabled | Raw envelope JSONL directory |
| `PIREPLAY_SESSION_NAME` | — | Stable artifact label |
| `PIREPLAY_COLLECTION_MODE` | `full` | `full` or `privacy` |
| `PIREPLAY_CAPABILITY_PROBE` | — | Developer golden-harness capability probe |

Every raw v2 envelope includes the hook name, timestamp, input, context, presence markers, correlation fields, collection mode, redaction metadata, and a sequence/checksum chain. The companion `.integrity.json` seal detects loss, truncation, or tampering. Existing directory modes are preserved; new transcript files use `0600`.

In `privacy` mode, replay-critical identifiers and structure are preserved but
content-bearing strings are replaced before writing. `redaction.fields` counts
the replacements and `redaction.applied` is true exactly when the count is
non-zero.

## Offline replay

```bash
cd pi/pireplay && npm run build
node dist/src/replay.js transcripts/session_x.jsonl --otel replays/spans_x.jsonl
```

The CLI accepts raw v2 transcripts and the bounded legacy canonical v1 format. It rejects mixed formats, verifies the source chain and integrity seal, then normalizes raw callbacks offline and writes explicit local `spans_*.jsonl`, `events_*.jsonl`, and `metrics_*.jsonl` artifacts.

Replay reporting is restricted to `http://127.0.0.1:4319/v1/{traces,metrics,logs}`. Production `OBSERV_PI_*` endpoints, authentication headers, and environment configuration cannot redirect replay egress.

`make -C pi uninstall-replay` removes only pireplay. It leaves `observ-pi`, its configuration, and recorded transcripts unchanged.
