# opencode-replay

Developer-only raw callback recorder and offline replay tooling for OpenCode.
It is a separate package, registration, configuration namespace, and lifecycle
from the user-facing `observ-opencode` production plugin.

The replay plugin never reads `observ-opencode.json`. Production
`enableTelemetry`, debug flags, exporter endpoints, and credentials cannot
enable replay capture. Live capture records versioned raw callback envelopes;
offline replay alone invokes the shared `observ-opencode/core` semantic engine.

## Explicit capture configuration

Capture is disabled by default. Enable it in
`~/.config/opencode/opencode-replay.json`:

```json
{
  "captureEnabled": true,
  "collectionMode": "full",
  "sessionDir": "/path/to/developer/transcripts",
  "debug": false
}
```

Replay-owned environment overrides are:

- `OPENCODE_REPLAY_CAPTURE_ENABLED=true`
- `OPENCODE_REPLAY_SESSION_DIR=/path`
- `OPENCODE_REPLAY_COLLECTION_MODE=full|privacy`
- `OPENCODE_REPLAY_DEBUG=true`
- `OPENCODE_REPLAY_CONFIG_FILE=/path/to/opencode-replay.json`

The legacy `enableTelemetry` key is accepted only in replay-owned config for a
bounded migration window and emits a deprecation warning. It is converted to
`captureEnabled` on the next installer update.

Former production/legacy variables such as `OPENCODE_SESSION_DIR`,
`OBSERV_OPENCODE_SESSION_DIR`, and `OPENCODE_DEBUG` are ignored and only emit a
migration warning. They never enable or configure replay.

No transcript path is resolved and no directory is created until capture is
explicitly enabled and the first callback with a session identifier arrives.
New directories/files use private modes; an existing directory keeps its mode.

## Raw schema and offline replay

New records use raw schema v2 and preserve hook name, timestamp, plugin ID,
correlation IDs, callback input/output availability, redaction metadata, and
the input/output payloads before semantic normalization. Credential-bearing
fields are redacted at the write boundary. The decoder continues to accept the
bounded legacy v0/v1 transcript formats; unsupported versions fail before the
semantic engine starts.

In `privacy` mode, replay-critical identifiers and structure remain available
for offline reconstruction while content-bearing strings are replaced before
the envelope is written. `redaction.fields` counts replacements and
`redaction.applied` is true exactly when that count is non-zero.

```bash
opencode-replay session_parent.jsonl session_child.jsonl --otel spans_subagent.jsonl
```

Explicit `--otel` files are local validation artifacts. Replay HTTP/report
traffic has one non-configurable destination:
`http://127.0.0.1:4319` with standard `/v1/{signal}` paths. `--config` and
`--profile` remote-report options are rejected, and production OTLP environment
variables or credentials are ignored.

## Install, uninstall, and cleanup

From `opencode/`:

```bash
make replay-plugin-install
make replay-plugin-uninstall
```

Install/update touches only replay registration, replay cache, and replay
config. Uninstall removes only replay registration and cache; it preserves
replay config and transcripts. To remove the replay config too:

```bash
make replay-plugin-uninstall PURGE_REPLAY_CONFIG=1
```

Transcript deletion remains an explicit developer action. Production
`observ-opencode` registration, package cache, configuration, and live
hook-to-OTel behavior are not modified by either replay lifecycle command.
