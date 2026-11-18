# OpenClaw Replay

`openclaw-replay` is a developer-only plugin for recording raw OpenClaw plugin callback boundaries and replaying them offline. It is a separate package, install, configuration namespace, and lifecycle from the user-facing `observ-openclaw` collection plugin.

It never registers a live telemetry processor and never changes `observ-openclaw` exporters. Installing or enabling production observability does not enable replay capture.

## Capture

Capture is disabled by default. A developer must install the replay plugin separately and opt in:

```bash
cd openclaw
make replay-plugin-install
```

Equivalent replay-owned configuration:

```json
{
  "plugins": {
    "entries": {
      "openclaw-replay": {
        "enabled": true,
        "hooks": { "allowConversationAccess": true },
        "config": {
          "captureEnabled": true,
          "sessionDir": "~/.aivision/ocw/transcripts",
          "collectionMode": "full",
          "stdout": false
        }
      }
    }
  }
}
```

| Field | Environment variable | Purpose |
| --- | --- | --- |
| `captureEnabled` | `OPENCLAW_REPLAY_CAPTURE_ENABLED` | Explicit developer capture opt-in |
| `sessionDir` | `OPENCLAW_REPLAY_SESSION_DIR` | Raw callback JSONL directory |
| `sessionName` | `OPENCLAW_REPLAY_SESSION_NAME` | Optional artifact label |
| `collectionMode` | `OPENCLAW_REPLAY_COLLECTION_MODE` | `full` or `privacy` |
| `stdout` | `OPENCLAW_REPLAY_STDOUT` | Emit raw envelopes to stdout; may be used without files |
| `debug` | `OPENCLAW_REPLAY_DEBUG` | Replay diagnostics only |

No directory is resolved or created while capture is disabled. Existing directory permissions are preserved. Files created by replay use mode `0600`.

Former `OBSERV_OPENCLAW_*` replay/session variables and the removed
`OPENCLAW_REPLAY_DIR` setting are ignored and only emit a migration warning;
they never enable or configure replay.

Each JSONL line is a versioned raw envelope containing callback input, context, optional output/error presence markers, correlation identifiers, collection mode, and credential redaction metadata. Semantic normalization remains in `@aivision/observ-openclaw` and runs only during offline replay.

The recorder covers the official **2026.7.1** and **2026.9.8** hook catalogs,
including the newer subagent progress, cron reconciliation, and Skill change
callbacks. Legacy `before_agent_start`, `subagent_spawning`, and `deactivate`
registrations remain for older hosts. Hosts ignore unknown hook registrations.
The versioned fixtures in `tests/fixtures/host-hook-contract/` record the npm
tarball integrity and the SDK declaration hash used for this compatibility check
(2026-10-05). Recording a callback does not imply an additional normalized span.

In `privacy` mode, replay-critical identifiers and structure are preserved but
content-bearing strings are replaced before writing. `redaction.fields` counts
the replacements and `redaction.applied` is true exactly when the count is
non-zero.

## Offline replay

```bash
openclaw-replay transcripts/session_x.jsonl --replay --otel replays/spans_x.jsonl
openclaw-replay transcripts/session_x.jsonl
openclaw-replay transcripts/session_x.jsonl --shift
```

`--replay --otel` writes an explicit local validation artifact and performs no HTTP export. Normal and shift reporting can send only to `http://127.0.0.1:4319/v1/traces`; endpoint, profile, config-file, environment credential, and header overrides are not supported.

`make replay-plugin-uninstall` disables and removes only replay plugin code. It does not uninstall or reconfigure `observ-openclaw`, and it preserves recorded transcripts.
