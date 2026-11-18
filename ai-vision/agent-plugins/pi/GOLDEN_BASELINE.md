# Piagent Golden baseline

Sanitized durable summary for the real run `2026-07-13T02-41-20-691Z-8902` (Pi 0.80.6, Node v26.4.0, post-rate-limit-retry + stable-baseline-promote improvements). Raw transcripts, model output, logs, and OTLP JSONL remain intentionally ignored because they can contain user/model content.

## Stable baseline

Starting from this run, the stable golden baseline lives in `~/.aivision/pi/golden/{transcripts,replays}/` (mirrors `~/.aivision/cc/golden/`). `make golden-promote` copies a successful run's transcripts+replays there; `make golden-replay-all` replays the stable transcripts; `make report-check-golden pi` POSTs the stable replays to AiVision.

| Scenario | Result | Evidence | Integrity |
|---|---|---|---|
| basic-llm | passed | 53 events; completed message; 0 tool ends | terminal seal sequence/checksum matched |
| tool-success | passed | 116 events; 1 successful tool end; completed message | seal matched |
| tool-error | passed | 128 events; 1 failed tool end; completed message; rate-limit retry 1 | seal matched |
| multi-tool | passed | 307 events; 4 tool ends; completed message | seal matched |
| subagent | skipped | no subagent tool among 7 configured tools | — |
| mcp-tool | skipped | no MCP tool among 7 configured tools | — |
| skill-call | skipped | no skill tool among 7 configured tools | — |

## Validation summary

- Transcript files: 4 (basic-llm, tool-success, tool-error, multi-tool).
- Replay files: 12 (spans/events/metrics for four real sessions).
- Realtime/replay parity: 26/26 spans, 34/34 events, 36/36 metrics; no differences.
- Local Weaver validate: 0 violations, 0 improvements, 6 semconv-information (`llm_missing_response_id`, optional field — model does not always return `responseId`).
- AiVision after mandatory clear and current-run-only POST: 0 findings at violation level, 12 information (`llm_missing_response_id`).
- Transcript seals: 4/4 present, complete, and equal to persisted terminal sequence/checksum.

## Improvements in this run

- **Rate-limit retry**: superloop now retries rate-limited scenarios (up to 4×, 15s delay) and cleans up partial transcripts + realtime OTLP between attempts so realtime/replay parity holds. `tool-error` passed on retry 1.
- **Stable golden baseline**: `pi/Makefile` gained `golden-promote`, `golden-replay-all`, `golden-check`, `golden-update`, `golden-init` targets so the top-level `make report-check-golden pi` works against a fixed baseline.
- **Replay idempotency**: `replay.ts` truncates target files before writing so repeated `replay-all` does not append duplicate export requests.

## Reproduce

```bash
make -C pi golden-evaluate                        # run 7 scenarios end-to-end
make -C pi golden-promote RUN_DIR=golden/runs/<id>  # promote to stable baseline
make report-check-golden pi                        # replay + POST stable baseline
```

Reproduce this exact baseline from the existing transcripts (no model calls):

```bash
make -C pi golden-re-evaluate RUN_DIR=golden/runs/2026-07-13T02-41-20-691Z-8902
```

The local-only detailed artifact path is `~/.aivision/pi/golden/runs/2026-07-13T02-41-20-691Z-8902/`.