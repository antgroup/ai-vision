# cchooks

Claude Code hook program for Langfuse observability, written in Rust.

## Features

- Reads Claude Code hook payload from stdin (with 1000ms timeout)
- Parses JSONL transcript files with offset-based incremental reading
- Builds conversation turns (user/assistant/tool_result)
- Posts traces/spans/generations to Langfuse API
- Turn-root span hierarchy: generation and tool spans are siblings
- Accurate tool timing: tool span endTime from tool_result timestamp
- File-based logging with rotation (10MB threshold, 10 backups)
- JSON state persistence (atomic write via tmp → rename)
- Gray/prod environment routing
- Cross-platform: darwin/linux/windows × arm64/x64

## Design Decisions

- **No tokio**: Pure synchronous I/O. stdin timeout uses `std::thread + mpsc::channel + recv_timeout`.
- **HTTP client**: `ureq` (synchronous, no async runtime, ~200KB overhead vs reqwest's ~1MB+).
- **TLS**: `rustls` (pure Rust, fully static, no system OpenSSL dependency).

## Build

```bash
# Common development build
cargo build --no-default-features --features common

# Common release build (current platform)
cargo build --release --no-default-features --features common --bin cchooks

# Cross-platform release (requires cargo-zigbuild and edition configuration)
make release
```

The default Cargo features select the AI Vision edition and read `aivision.keys.json` from this crate directory. Build the common edition with `cargo build --no-default-features --features common` to use runtime configuration.

## Environment Variables

### 主配置

| Variable | Description | Default |
|---|---|---|
| `LANGFUSE_ENV` | Set to `gray` for gray environment | prod |
| `ANT_GRAY` | Set to `true` for gray environment | false |
| `LANGFUSE_HOST` | Override Langfuse host URL | (built-in) |
| `LANGFUSE_PUBLIC_KEY` | Override Langfuse public key | (built-in) |
| `LANGFUSE_SECRET_KEY` | Override Langfuse secret key | (built-in) |
| `LANGFUSE_ATTR_USERNAME` | Username for Langfuse traces | `$USER` |
| `LANGFUSE_METADATA` | JSON string of extra metadata | - |
| `LANGFUSE_TAGS` | Comma-separated tags | - |
| `LANGFUSE_ATTR_*` | Custom metadata attributes | - |
| `CCHOOKS_DEBUG` | Set to `true` for debug logging | false |
| `CCHOOKS_STATE_DIR` | Override state directory | edition root 下的 `state/` |
| `CCHOOKS_STRUCTURED_OUTPUT` | Enable two-phase Stop coordination for callers using SDK `output_format`. Accepts only case-insensitive, whitespace-trimmed `true`/`1` and `false`/`0`; invalid values fail closed with a bounded diagnostic | `false` |
| `CCHOOKS_REALTIME_EXPORT` | Enable or disable payload-driven completed-tool-span export and near-realtime generation export. Accepts boolean/numeric or string `true`/`1` and `false`/`0`; unset or invalid values use the default | `true` |
| `CCHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE` | Positive number of distinct assistant `message.id` rounds required before one generation export. Accepts numeric or string values; invalid values fall back to the default | `3` |
| `CCHOOKS_RETRY_POLICY` | Turn export policy: `at_least_once` checkpoints after 2xx and retries from a later Stop/SessionEnd; `at_most_once` checkpoints before POST and never retries a failed attempt | `at_least_once` |
| `CCHOOKS_STOP_TRANSCRIPT_SETTLE_MS` | Maximum time for Stop/StopFailure to poll every 100ms for the latest terminal turn; processing starts immediately once found | `2000` |
| `CCHOOKS_TRANSCRIPT_CAPTURE` | Preserve the legacy Stop behavior that copies the host transcript into the edition `transcripts/` directory. Set to `false`/`0` only when the embedder owns transcript retention and wants to disable this extra copy | `true` |
| `CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST` | For embedders with host-owned ephemeral transcripts, keep the parent hook alive only until the bounded current-turn window is read into worker memory. Network export remains asynchronous and timeout is fail-open | `false` |
| `CCHOOKS_TRANSCRIPT_INGEST_WAIT_MS` | Maximum local-ingest barrier wait when `CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST=true` | `3000` |
| `CCHOOKS_STOP_HTTP_TIMEOUT_MS` | Maximum timeout in milliseconds for one turn POST; capped by the hook's remaining total budget | `20000` |
| `CCHOOKS_VCS_NO_CACHE` | Refresh the full remote snapshot on every `UserPromptSubmit`; branch is always refreshed | `false` |
| `CLAUDE_PROJECTS_DIR` | Override Claude projects directory | `~/.claude/projects` |

`CCHOOKS_RETRY_POLICY` controls client-side compensation across Stop,
StopFailure, and SessionEnd hooks; it does not provide exactly-once delivery:

- `at_least_once` is the default. Network failures and non-2xx responses leave
  the turn retryable. If the server accepted the request but the response was
  lost, the turn can appear more than once.
- `at_most_once` durably accounts for the turn before POST. A failed request is
  not retried, preventing client-side duplicate sends at the cost of possible
  trace loss.

Callers that configure Claude Agent SDK `output_format` must also set
`CCHOOKS_STRUCTURED_OUTPUT=true` in the Claude Code child environment. The
first `Stop(false)` is then retained as `awaiting_continuation` without running
finalization handlers, and the matching `Stop(true)` authorizes the existing
finalization path. Calls without `output_format` should omit the variable (or
set `false`/`0`) and keep the legacy immediate Stop path. Unmarked third-party
`output_format` calls cannot be distinguished deterministically from an
ordinary Stop.

Realtime export is enabled by default. `CCHOOKS_REALTIME_EXPORT=false` or `0`
disables it and preserves the Stop-only path. When enabled, the same
retry-policy tradeoff applies at tool granularity. `PostToolUse` and
`PostToolUseFailure` build completed tool
spans directly from the hook payload and persisted IDs; they do not scan or copy
the transcript. Stop remains enabled as the authoritative finalizer, reuses the
matched trace/root/tool IDs, fills missing spans, and skips tool spans already
accounted for by realtime delivery.

Realtime export also enables the generation overlay. It is
near-realtime rather than token streaming: after
`PostToolUse`/`PostToolUseFailure`, cchooks performs one bounded scan of the
hook-provided transcript and counts completed generation rounds by distinct
assistant `message.id`. It sends the largest contiguous full prefix once the
session-scoped batch threshold is reached. The default threshold is `3`;
`1` sends every eligible round. Parallel tools in one assistant message count
as one round. The effective threshold is snapshotted in session state, so an
environment change affects new sessions rather than changing an active batch.
Sub-threshold, delayed, partial, or lock-contended generations remain for Stop
compensation. No transcript settle loop or transcript copy is introduced.

The realtime overlay requires `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
and `PostToolUseFailure` registrations. Configure tool hooks as asynchronous at
the Claude Code layer, without changing Stop/SessionEnd registration. Deployments
that have not registered these events can explicitly set
`CCHOOKS_REALTIME_EXPORT=false` until their hook configuration is updated.

## Session-isolated state

cchooks stores mutable runtime state in one shard per Claude Code session:

```text
<state-dir>/sessions/<sanitized-prefix>-<sha256-suffix>/
├── state.json
└── state.json.lock
```

The default `<state-dir>` is edition-specific:

- aivision: `~/.aivision/cc/state`
- common/default: `~/.claude/state`

The readable prefix is diagnostic only; the hash suffix is derived from the
original opaque session ID and prevents path traversal and sanitized-name
collisions. Newly created session directories and files are private on Unix.
Existing parent-directory permissions are not changed.

Timing, repository resource context, tool context, realtime checkpoints,
generation claims, subagent state, turn-digest state, and Stop cursors share
the same session shard. Dynamic session artifacts use the same
collision-resistant session key below `<state-dir>/dynamic_sessions/`. A slow
or locked session therefore does not share a mutable JSON file or lock with
unrelated sessions.

`UserPromptSubmit` is the sole owner of repository resource discovery. It
captures one bounded `git remote -v` snapshot, selects remotes
case-insensitively by `origin > fetch > lexical`,
refreshes the branch, and stores `{remote_url, branch, cwd}` in the current
session shard. Non-`origin` repositories are supported. Stop-family, realtime
tool/generation, Bash tool context, async subagent, and turn-digest exporters
only consume the cwd-compatible persisted snapshot and emit
`vcs.repository.url.full` / `vcs.ref.head.name`; they never run Git as a
fallback.

Stop, StopFailure, and SessionEnd hold the session lock only for state
snapshots/checkpoints. Transcript polling and copying, OTel construction,
debug archive writes, and HTTP requests run after the lock is released.

### Delivery and liveness boundaries

- `at_least_once` still permits a duplicate POST when two same-session Stops
  race between remote acceptance and checkpoint. Duplicate attempts reuse the
  same deterministic turn/trace/span identities, and the persisted cursor and
  turn count advance monotonically.
- `at_most_once` checkpoints before POST, so concurrent Stops allow one send
  attempt. Its documented loss window after checkpoint and before/during POST
  remains unchanged.
- If a session lock cannot be acquired, cchooks performs no state-derived
  export and no unlocked read/write. It logs a bounded diagnostic and returns
  through the existing fail-open hook contract.
- This release does not add a recovery daemon, lease, global queue, or spool.
  A killed Stop, or a skipped Stop with no later compatible lifecycle hook, is
  not guaranteed to complete automatically.

### Upgrade and rollback note

This change intentionally does not migrate the legacy global
`<state-dir>/cchooks_state.json` or legacy auxiliary files. The files are left
untouched, and the session-sharded runtime does not read or lock them. Existing
sessions therefore establish new shard-local checkpoints when first observed.

Rolling back to a legacy binary makes the retained global file authoritative
again; progress written only to session shards is not transferred back. A
later migration/retention design should be reviewed separately before legacy
state is imported or removed.

## Testing

```bash
# 运行所有测试（单元测试 + 集成测试）
cargo test --no-default-features --features common --all-targets

# 仅运行单元测试
cargo test --no-default-features --features common --lib

# 仅运行集成测试
cargo test --no-default-features --features common --test transcript_integration
cargo test --no-default-features --features common --test state_integration

# 运行 replay 工具测试
cargo test --no-default-features --features common --bin cchooks-replay
```

测试覆盖的模块：
- [transcript 模块](src/common/transcript/) — JSONL 解析、turn 构建、文本提取、skill 识别
- **state.rs** — 状态加载/保存、原子写入、session 隔离、损坏恢复
- **hook.rs** — session ID 提取、transcript 路径解析、环境变量处理
- **logger.rs** — ISO 时间戳验证、日期转换、闰年判断
- **otel.rs** — 时间戳转纳秒、span 构建、OTel 导出请求结构

## Replay 工具

`cchooks-replay` 是一个命令行工具，用于回放 JSONL transcript 文件，以终端树形方式可视化展示 trace 链路。

### 编译

```bash
cargo build --no-default-features --features common --bin cchooks-replay --release
```

### 使用

```bash
# 树形展示 trace 链路（带颜色）
cchooks-replay <transcript.jsonl>

# 禁用颜色输出
cchooks-replay <transcript.jsonl> --no-color

# 显示完整文本（不截断）
cchooks-replay <transcript.jsonl> --verbose

# 仅显示摘要统计
cchooks-replay <transcript.jsonl> --summary

# 仅显示第 N 轮对话
cchooks-replay <transcript.jsonl> --turn 2

# 组合使用
cchooks-replay <transcript.jsonl> --summary --no-color

# 写入 OTLP JSONL，供 weaver-guard 离线校验
target/release/cchooks-replay path/to/transcript.jsonl --otel spans_session.jsonl
```

### 输出示例

**树形模式：**
```
Trace Tree
├── Turn 1 [2026-03-27T10:00:00.000Z]
│   ├── 👤 User: What is 2+2?
│   └── 🤖 Assistant (claude-sonnet-4-20250514) [in:10 out:8]
│       └── The answer is 4.
└── Turn 2 [2026-03-27T10:01:00.000Z]
    ├── 👤 User: Read config.json
    ├── 🤖 Assistant (claude-sonnet-4-20250514) [in:15 out:20]
    │   ├── Let me read that.
    │   ├── 🔧 Read
    │   └── 🔧 Write
    └── 🔧 1 tool result(s)
```

**摘要模式：**
```
Summary
  Turns: 2
  Assistant messages: 2
  Tool calls: 2
  Tool results: 1
  Tokens: input=25 output=28 cache_write=0 cache_read=0
  Models: claude-sonnet-4-20250514
  Tool breakdown:
    🔧 Read (1)
    🔧 Write (1)
  Time span: 2026-03-27T10:00:00.000Z → 2026-03-27T10:01:01.000Z
```

## Dependencies

- `rustup` (Rust toolchain)
- `cross` (for cross-platform compilation): `cargo install cross`
- Docker (required by `cross` for cross-compilation)
