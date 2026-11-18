# Wire fixture provenance

These files are **sanitized protocol regression fixtures**, not byte-for-byte native captures or acceptance evidence. They contain no credentials or personal sessions. Integration tests read them from the checkout and write exports only to unique temporary directories. Original CLI captures, provider requests, source-oracle evidence and acceptance artifacts remain outside the repository and are not included in this change.

All workstation and run-directory paths are normalized to `/fixture/work` and `/fixture/work/receipt.txt`. JSON is serialized canonically after sanitization. Timestamps, event ordering, tool IDs, usage values and the wire fields exercised by tests are retained unless explicitly noted below. The `native-` filename prefix identifies their captured protocol origin; it does not imply that these checked-in files are unmodified evidence.

## Kimi Code 2.1.1

Official source: [`MoonshotAI/kimi-code@f67e6398fb3210ad8ace970e2dfd5bcc984ed61f`](https://github.com/MoonshotAI/kimi-code/tree/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f), release `@moonshot-ai/kimi-code@2.1.1`, published 2026-09-24. The isolated captures used the official `kimi-code-darwin-arm64.tar.gz` release asset (SHA256 `8c3bad99571b16abd113ab5d511a98a36b852c0d5493991842a67b8c2bc4ecd0`; `kimi --version` returned `2.1.1`), a loopback fake provider and a controlled receipt file. No remote model or personal profile was used.

- `main.wire.jsonl` derives from upstream [`apps/vis/server/test/fixtures/sessions/sample-main/agents/main/wire.jsonl`](https://github.com/MoonshotAI/kimi-code/blob/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f/apps/vis/server/test/fixtures/sessions/sample-main/agents/main/wire.jsonl). Its public sample cwd is normalized to `/fixture/work`. It exercises the older Code wire 1.1 layout.
- `native-tool-loop.wire.jsonl` derives from a 2026-10-05 native Read session with two provider requests. It retains metadata, turn.prompt, context.append_message, context.append_loop_event, llm.request, usage.record and turn.ended. Read arguments and display paths are sanitized; these are call arguments, not the tool schema covered by `toolsHash`. The receipt text `KIMI_NATIVE_211_READ_OK` is controlled test data.
- `native-unknown-usage.wire.jsonl`, `native-zero-usage.wire.jsonl` and `native-cache-usage.wire.jsonl` retain only the records needed to exercise usage source boundaries: metadata, turn.prompt, llm.request, usage.record, turn.ended and step.begin/step.end. Prompt paths are sanitized. Request metadata contains message counts, not a hash of the changed user prompt. The host emits indistinguishable four-zero usage for absent HTTP usage and explicit zero, and loses cache-write/reasoning detail. These fixtures test that information loss; they are not complete tool-loop acceptance evidence.
- `native-system-bound.wire.jsonl` is a deliberately minimized derived fixture. The original full profile prompt is replaced with the short synthetic text `You are a test assistant. The working directory is /fixture/work.`; environmentDisclosure.cwd is `/fixture/work`. Every associated `llm.request.systemPromptHash` is recomputed from that exact UTF-8 prompt. The positive hash-binding test and synthetic mismatched-hash negative test therefore remain internally consistent without publishing the captured system prompt or local paths.

System hashes in fixtures that omit profile.bind are opaque source metadata; the tests do not claim to reconstruct or validate the absent system prompt. No hash is presented as proof that sanitized fixture bytes equal the original private evidence.

Current sanitized file SHA256 values:

| Fixture | SHA256 |
| --- | --- |
| `main.wire.jsonl` | `3554e4f64e3b308fa02540f4af33911a54a7259b97ec6abd58e00aa1815fed21` |
| `native-cache-usage.wire.jsonl` | `2e26024bda8b7c6ff49cdcf806a88e784b1d114bdee8d51d6fb8cb491c96d664` |
| `native-system-bound.wire.jsonl` | `61bdf3a5adfaea0e72900601e0f04d2046816dc1de69b7330c546f2a3af6b5e9` |
| `native-tool-loop.wire.jsonl` | `87e9d552c7858a92930bc06e1eb16d1e288b23647a48e424327b0df608a0a2b4` |
| `native-unknown-usage.wire.jsonl` | `db5ebab19bbf8758a11c185c5927b2027192c741d48b09356f0396be05a5e902` |
| `native-zero-usage.wire.jsonl` | `d9da7874c885cdaafb05ce75667fd1399626617bddc498652f0f85222db7250c` |

Source contracts used for synthetic unit cases: `packages/agent-core-v2/src/agent/loop/turnEvents.ts`, `agent/contextMemory/types.ts`, `llm-adapter/model/model-requester-impl.ts`, and `_base/utils/workdir-slug.ts` at the same commit. They cover missing/zero/cache usage, tool failure, cancellation, timing and workspace path normalization. These unit cases are not additional native CLI runs.

## Legacy Python wire

`legacy-wire/wire_basic.jsonl`, `wire_tools.jsonl`, and `wire_advanced.jsonl` are fixed **synthetic protocol contracts**, not captured sessions. They replace tests' previous dependence on ignored transcript files and retain the wire 1.9 envelope. Fields match existing parser unit cases and upstream 1.52.0 [`tests/core/test_wire_message.py`](https://github.com/MoonshotAI/kimi-cli/blob/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82/tests/core/test_wire_message.py) / [`src/kimi_cli/wire/types.py`](https://github.com/MoonshotAI/kimi-cli/blob/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82/src/kimi_cli/wire/types.py). Timestamps, text, IDs and token values are controlled test data. The advanced contract covers compaction, retry and nested subagent reconstruction; it does not claim those paths passed current semconv validation or were rerun in the latest native Python CLI.
