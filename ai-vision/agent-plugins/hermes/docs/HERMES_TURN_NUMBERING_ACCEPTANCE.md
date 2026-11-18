# Native resumed-turn acceptance

Local acceptance on 2026-10-15 used macOS, Python 3.12, and official Hermes
0.21.5 at release commit `f97608f178d1ffeca59860195ab7da295f7c8e5f`.
The production plugin and independent raw-hook recorder loaded through the
official directory extension mechanism in an isolated `HERMES_HOME`. The host
executed real file tools and persisted/resumed its own conversation. A local
HTTP provider supplied deterministic responses and recorded every physical call.
This validates native execution and collector output; it is not a live-model
or AI Vision storage/API acceptance result.

## Results

| Case | Processes / user turns | Physical calls | Original OTLP envelopes | Result |
| --- | --- | ---: | ---: | --- |
| CLI resume plus length continuation | 3 / 3 | 8 | 71 | 51 checks passed; ordinals `1, 2, 3` |
| ACP resume plus automatic compression | 2 / 3 | 9 | 77 | 59 checks passed; ordinals `1, 2, 3` |
| Same compression probe against core/plugin 0.2.1 at `historical-source-revision-redacted` | 2 / 3 | 9 | — | Host completed; only root/child ordinal checks failed, with `1, 2, 1` |

Each run used a new home and output directory. The negative baseline used the
same host and probe with the previous collector installed; it did not mutate
host code, native callbacks, or session state to simulate a reset.

The compression run starts two user turns in one ACP process, exits, and loads
the same native session in a second process. It enables the documented
`compression.enabled`, `in_place`, and `threshold_tokens` settings before the
third real user prompt. Request history shrank from 12 to 8 messages; the native
final history contains one `_compressed_summary`, removes the old first user
prompt, and retains the current prompt. The summary request has the third
turn's original session/turn IDs. All nine requests, including title generation
and compression, match their own LLM input/output and usage; internal
continuation and `/context` do not create another user turn.

Both final reports were independently checked against their original OTLP
payloads. The source oracle verifies native request identities and available
start/end times, trace parents, resource identity, receipt/tool results, and
physical request/token counters. Negative controls reject missing or changed
OTLP, wrong IDs/times/ordinals, missing terminal callbacks, unchanged history,
extra turns, process reuse, and boolean ordinals. Full input/output is bound
per physical request across native hooks, HTTP, spans, and API logs, including
summary/title requests, reasoning text, and tool calls. The independent review
confirmed these checks reject changes even when original OTLP and its parsed
view are modified together.

The 89 affected harness tests passed. A broader 327-test run reported two
process-cleanup errors in unchanged Kimi/OpenCode tests (`killpg` permission
error and a `ps` timeout); both tests passed when rerun individually. That
broader run is not claimed as an all-green suite. Capability references,
semantic-spec synchronization, hook inventory, local documentation links and
`git diff --check` also passed.

Repository `weaver-guard` 0.2.6 validated the combined original payloads from
both final runs: 148 signals / 148 samples, zero violations and zero semconv
issues. Its 34 improvement suggestions are non-failing recommendations. The
gate also verified exact sample counts and no skipped/evaluation-error inputs.
The validator ran locally in a Linux container; this does not turn the macOS
Hermes runs into Linux native-agent acceptance.

## Reproduce

Use the pinned runtime installation described in the
[native harness README](../../tests/native_agent_e2e/README.md), then:

```bash
make -C ai-vision/agent-plugins/tests/native_agent_e2e hermes-turn-numbering \
  RUNTIMES=/tmp/aivision-native-runtimes/runtimes.json \
  OUT=/tmp/aivision-hermes-turn-numbering
make -C ai-vision/agent-plugins/tests/native_agent_e2e hermes-compression \
  RUNTIMES=/tmp/aivision-native-runtimes/runtimes.json \
  OUT=/tmp/aivision-hermes-compression
```

Each `OUT` must be new. `report.json` retains raw callbacks, original OTLP,
physical HTTP evidence and individual check results. Runtime artifacts are not
committed. The final local report SHA-256 digests are:

| Case | SHA-256 |
| --- | --- |
| CLI resume | `20645180e85b09f7d4ea4be5a05e3fbe935316e72f0bfb095e610fbc96754d99` |
| ACP compression | `8fdcca158212f79c5d5d95bd635d59049aad85048504749330e9cf78d1c20728` |

## Boundaries

- Hermes 0.21.5 continues its one `finish_reason=length` response before its
  normal terminal hook. The wire response, content and usage are verified, but
  that one native end time is not independently available. Its exact request
  ID remains in `unverified_native_end_ids`; the oracle permits no other missing
  terminal callback.
- Manual ACP `/compress` executes outside a user turn and emits auxiliary hooks
  with empty session/task/turn IDs. The collector reports that missing identity
  instead of attributing the request to a guessed turn. The passing case uses
  real automatic compression inside an active turn.
- Waiting for idle compression alone did not trigger it on this host: starting
  a turn refreshes the activity timestamp before the idle check. Acceptance
  requires observed history changes from the threshold trigger.
- These native runs were local macOS runs. The probes are wired into the
  Linux/macOS/Windows CI matrix, but job configuration is not execution evidence.
  At PR head `historical-source-revision-redacted`, GitHub blocked jobs before execution because of
  account payments/spending limits.
