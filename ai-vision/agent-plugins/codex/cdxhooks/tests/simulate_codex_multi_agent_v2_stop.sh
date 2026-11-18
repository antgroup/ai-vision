#!/usr/bin/env bash
# End-to-end regression for Codex multi-agent v2 child correlation and
# export/state transaction semantics.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDXHOOKS_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN="$CDXHOOKS_REPO/target/debug/cdxhooks"
PARENT_FIXTURE="$SCRIPT_DIR/fixtures/codex_multi_agent_v2_parent.jsonl"
CHILD_FIXTURE="$SCRIPT_DIR/fixtures/codex_multi_agent_v2_child.jsonl"

for file in "$BIN" "$PARENT_FIXTURE" "$CHILD_FIXTURE"; do
    [ -f "$file" ] || { echo "ERROR: required file missing: $file" >&2; exit 1; }
done

TMPDIR_BASE="$(mktemp -d -t cdxhooks-v2-sim-XXXXXX)"
SESSIONS_DIR="$TMPDIR_BASE/sessions"
STATE_FILE=""
REQUEST_COUNT="$TMPDIR_BASE/request-count"
mkdir -p "$SESSIONS_DIR" "$TMPDIR_BASE/state" "$TMPDIR_BASE/spool" "$TMPDIR_BASE/logs"
cp "$PARENT_FIXTURE" "$SESSIONS_DIR/parent-v2.jsonl"
cp "$CHILD_FIXTURE" "$SESSIONS_DIR/child-v2.jsonl"

export CDXHOOKS_STATE_DIR="$TMPDIR_BASE/state"
export CDXHOOKS_SPOOL_DIR="$TMPDIR_BASE/spool"
export CDXHOOKS_LOG_DIR="$TMPDIR_BASE/logs"
export CDXHOOKS_DEBUG="true"
export CDXHOOKS_TEST_DISABLE_FOREGROUND_WORKER=1
export CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS=750
export CDXHOOKS_STOP_BUDGET_MS=5000
export CODEX_SESSIONS_DIR="$SESSIONS_DIR"
export CDXHOOKS_LANGFUSE_PUBLIC_KEY="fixture-pk"
export CDXHOOKS_LANGFUSE_SECRET_KEY="fixture-sk"
export CDXHOOKS_LANGFUSE_BASE_URL="http://127.0.0.1:1"
export CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:1/v1/traces"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"

SERVER_PID=""
cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

emit_stop() {
    local sid="$1"
    local transcript="$2"
    printf '{"hook_event_name":"Stop","session_id":"%s","transcript_path":"%s"}\n' \
        "$sid" "$transcript" | "$BIN" >/dev/null 2>&1 || true
}

drain_worker() {
    local sid="$1"
    CDXHOOKS_WORKER_SESSION_ID="$sid" \
        "$BIN" worker run --max-jobs 10 >/dev/null 2>&1 || true
}

assert_no_committed_parent_state() {
    if [ -z "$STATE_FILE" ]; then
        STATE_FILE="$(find "$TMPDIR_BASE/state" -name state_stop.json -type f -print -quit)"
    fi
    if [ -f "$STATE_FILE" ] && [ "$(jq -r '.turn_count // 0' "$STATE_FILE")" -ne 0 ]; then
        echo "FAIL: parent state advanced before a successful complete export" >&2
        exit 1
    fi
}

assert_failed_reason() {
    local expected="$1"
    local state_reason log_matches
    for _ in $(seq 1 250); do
        STATE_FILE="$(find "$TMPDIR_BASE/state" -name state_stop.json -type f -print -quit)"
        state_reason=""
        if [ -n "$STATE_FILE" ]; then
            state_reason="$(jq -r '.pending_export_reason // ""' "$STATE_FILE")"
        fi
        log_matches="$(cat "$TMPDIR_BASE"/logs/cdxhooks*.log 2>/dev/null \
            | grep -c "$expected" || true)"
        if [[ "$state_reason" == *"$expected"* || "$log_matches" -ge 1 ]]; then
            return 0
        fi
        sleep 0.02
    done
    echo "FAIL: expected a failed finalizer containing reason: $expected" >&2
    exit 1
}

wait_for_stop_intent_clear() {
    local intent
    intent="$(dirname "$STATE_FILE")/state_stop_intent.json"
    for _ in $(seq 1 300); do
        [ ! -f "$intent" ] && return 0
        sleep 0.02
    done
    echo "FAIL: Stop intent was not cleared after completed export" >&2
    exit 1
}

# Child Stop is suppressed through v2 activity/path correlation and triggers
# a parent retry. With the parent still missing its typed terminal/final lines,
# that retry must retain pending input without committing a turn.
head -n 13 "$PARENT_FIXTURE" > "$SESSIONS_DIR/parent-v2.jsonl"
emit_stop "child-v2" "$SESSIONS_DIR/child-v2.jsonl"
emit_stop "parent-v2" "$SESSIONS_DIR/child-v2.jsonl"
drain_worker "parent-v2"
assert_no_committed_parent_state
cp "$PARENT_FIXTURE" "$SESSIONS_DIR/parent-v2.jsonl"

# A completed parent with an expected but absent child must defer instead of
# permanently posting a parent-only trace.
rm "$SESSIONS_DIR/child-v2.jsonl"
emit_stop "parent-v2" "$SESSIONS_DIR/parent-v2.jsonl"
drain_worker "parent-v2"
assert_no_committed_parent_state
assert_failed_reason "subagent_transcript_not_ready"

# A child file that is initially missing its final newline is not readable as
# a complete JSONL record. Let it grow during the bounded retry: the merge may
# proceed only after the writer settles and the trailing buffer disappears.
CHILD_BYTES="$(wc -c < "$CHILD_FIXTURE" | tr -d ' ')"
dd if="$CHILD_FIXTURE" of="$SESSIONS_DIR/child-v2.jsonl" bs=1 count="$((CHILD_BYTES - 1))" 2>/dev/null
(sleep 0.1; printf '\n' >> "$SESSIONS_DIR/child-v2.jsonl") &
emit_stop "parent-v2" "$SESSIONS_DIR/parent-v2.jsonl"
drain_worker "parent-v2"
wait
assert_no_committed_parent_state
assert_failed_reason "otel_export_failed"

# Start a one-request local OTLP sink for the successful retry.
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
python3 "$SCRIPT_DIR/otel_test_sink.py" "$PORT" "$REQUEST_COUNT" 1 &
SERVER_PID=$!
sleep 0.1
export CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:$PORT/v1/traces"
emit_stop "parent-v2" "$SESSIONS_DIR/parent-v2.jsonl"
drain_worker "parent-v2"
wait "$SERVER_PID"
SERVER_PID=""

test -f "$STATE_FILE"
test "$(jq -r '.turn_count' "$STATE_FILE")" -eq 1
test "$(jq -r '.parent_stopped' "$STATE_FILE")" = "true"
test "$(jq -r '.merged_subagent_ids | length' "$STATE_FILE")" -eq 1
test "$(jq -r '.merged_subagent_ids[0]' "$STATE_FILE")" = "child-v2"
test "$(jq -r '.pending_export_reason // ""' "$STATE_FILE")" = ""
test "$(wc -l < "$REQUEST_COUNT" | tr -d ' ')" -eq 1

# Duplicate parent Stop sees the committed turn and must not POST again even
# though the one-shot server is gone.
emit_stop "parent-v2" "$SESSIONS_DIR/parent-v2.jsonl"
drain_worker "parent-v2"
wait_for_stop_intent_clear
test "$(jq -r '.turn_count' "$STATE_FILE")" -eq 1
test "$(wc -l < "$REQUEST_COUNT" | tr -d ' ')" -eq 1

LOG_FILE="$TMPDIR_BASE/merged.log"
cat "$TMPDIR_BASE"/logs/cdxhooks*.log > "$LOG_FILE" 2>/dev/null || true
grep -q "until child transcript(s) are ready: child-v2" "$LOG_FILE"
grep -q "Langfuse OTel HTTP request failed: io: Connection refused" "$LOG_FILE"
grep -q "Merging 1 child session(s) into parent OTel" "$LOG_FILE"

echo "PASS: v2 parent finalizer recovery, growing-file defer/retry, merge, atomic commit, and Stop dedupe"
rm -rf "$TMPDIR_BASE"
