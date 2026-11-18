# Hermes common-config/privacy verification

This matrix is the release-candidate evidence index for the 23 acceptance
criteria in the accepted Hermes alignment plan. Runtime artifacts are generated
in private temporary roots; only versioned fixtures and tests are committed.

| AC | Evidence |
| ---: | --- |
| 1 | `test_config`, `test_common_schema_alignment`: all production keys are common-schema keys; legacy/replay keys fail with field paths. |
| 2 | `docs/config/hermes.example.jsonc` and both edition files pass strict schema validation. |
| 3 | `test_scalar_map_and_signal_precedence_is_deterministic` proves defaults → edition → user JSON → generic env → plugin env. |
| 4 | `test_configured_headers_and_attributes_reach_every_json_signal` proves signal headers win collisions. |
| 5 | The same serialized-boundary test proves configured signal/resource attributes reach all three signals. |
| 6 | `test_privacy_boundary` proves Resource identity exclusion plus dynamic and static emission-time identity behavior. |
| 7 | `test_protocol_spike` parses all JSON/protobuf signal payloads; invalid protocol config fails closed. |
| 8 | Hostile full/privacy JSON/protobuf tests and live/replay artifact scans prove privacy content removal with structural fields retained. |
| 9 | `test_debug_keeps_http_and_creates_no_replay_artifacts` proves debug is diagnostic-only. |
| 10 | Production hook/telemetry tests prove no replay installation or artifact is required. |
| 11 | Replay disabled registration and isolated install tests prove install alone creates no capture hooks or artifacts. |
| 12 | Replay capture/privacy and artifact permission tests prove redact-before-write, `0600` files, and unchanged existing directory modes. |
| 13 | Replay report endpoint tests allow only `http://127.0.0.1:4319` standard signal paths and expose no header override. |
| 14 | Real temporary-venv tests prove `make install hms` has no replay plugin or commands; explicit replay install exposes exactly three commands. |
| 15 | Fresh full/privacy live and replay validation: zero violations, zero semconv issues; full/privacy golden comparisons pass 3/3 files. |
| 16 | AST, wheel, metadata, entry-point, and independent-import tests prove bidirectional product boundaries. |
| 17 | Migration tests prove allowlist-only idempotent migration, existing JSON preservation, private backup, and rollback recovery. |
| 18 | Disabled registration creates no runtime; per-signal disable tests suppress only the selected signal for both protocols. |
| 19 | Wire tests prove delta=`1` and cumulative=`2` for JSON and protobuf metrics. |
| 20 | Strict peer schema tests cover OpenClaw, OpenCode, Pi, and Hermes; runtime key/enum inventory matches the schema. |
| 21 | Cohort tests prove fail-before-mutation one-sided rejection, byte-preserving injected failures, rollback, sibling retention, last-consumer core removal, and surviving imports. |
| 22 | OpenCode Cowork metadata separation/generation tests prove internal metadata cannot enter public runtime config. |
| 23 | Functional isolated tests execute all eight standard Make targets and assert replay/report/weaver ownership plus safe clean behavior. |

Additional gates:

- `hermes-agent==0.18.2` installed contract fixture test: pass.
- Core, production, and replay wheel builds: pass.
- Full Hermes isolated suite: pass with the local-runtime version check replaced
  by the explicit pinned-package run above.
- Privacy artifact canary scan: zero content/credential matches.
- Full artifact credential scan: zero credential matches.
