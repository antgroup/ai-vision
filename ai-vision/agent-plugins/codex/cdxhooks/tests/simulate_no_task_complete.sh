#!/usr/bin/env bash
# simulate_no_task_complete.sh
#
# Regression test for terminal-only export. A transcript without the trailing
# task_complete event must remain pending on Stop. After task_complete is
# appended, SessionEnd must export it exactly once through the shared drain
# path.
#
# Assertions:
#   - zero POST attempts during the first Stop
#   - exactly one POST after task_complete + SessionEnd
#   - state.turn_count incremented and pending_messages drained

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDXHOOKS_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

BIN="$CDXHOOKS_REPO/target/debug/cdxhooks"
PARENT_SRC="$SCRIPT_DIR/fixtures/codex_multi_agent_v2_parent.jsonl"
CHILD_SRC="$SCRIPT_DIR/fixtures/codex_multi_agent_v2_child.jsonl"

[ -f "$BIN" ]        || { echo "ERROR: missing $BIN" >&2; exit 1; }
[ -f "$PARENT_SRC" ] || { echo "ERROR: missing $PARENT_SRC" >&2; exit 1; }
[ -f "$CHILD_SRC" ]  || { echo "ERROR: missing $CHILD_SRC" >&2; exit 1; }

PARENT_SID="parent-v2"
CHILD_SID="child-v2"

TMPDIR_BASE="$(mktemp -d -t cdxhooks-noct-XXXXXX)"
TRANSCRIPT_DIR="$TMPDIR_BASE/sessions"
mkdir -p "$TRANSCRIPT_DIR"
export CDXHOOKS_STATE_DIR="$TMPDIR_BASE/state"
export CDXHOOKS_SPOOL_DIR="$TMPDIR_BASE/spool"
export CDXHOOKS_LOG_DIR="$TMPDIR_BASE/logs"
mkdir -p "$CDXHOOKS_STATE_DIR" "$CDXHOOKS_SPOOL_DIR" "$CDXHOOKS_LOG_DIR"
LOG_FILE="$TMPDIR_BASE/merged.log"
FIRST_LOG_FILE="$TMPDIR_BASE/first-stop.log"
REQUEST_COUNT="$TMPDIR_BASE/request-count"

export CDXHOOKS_LANGFUSE_BASE_URL="http://127.0.0.1:1"
export CDXHOOKS_LANGFUSE_PUBLIC_KEY="sim-pk"
export CDXHOOKS_LANGFUSE_SECRET_KEY="sim-sk"
export CDXHOOKS_TEST_DISABLE_FOREGROUND_WORKER=1
export CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS=750
export CDXHOOKS_STOP_BUDGET_MS=2000
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
python3 "$SCRIPT_DIR/otel_test_sink.py" "$PORT" "$REQUEST_COUNT" 1 &
SERVER_PID=$!
cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
sleep 0.1
export CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:$PORT/v1/traces"

# Strip the trailing task_complete (last line) from the versioned fixture. Result:
# parent transcript that has every wait_agent output + final agent_message +
# token_count but NO task_complete — mimics the Codex Desktop silence window.
# (cp child transcript so directory-scan child-discovery works.)
PARENT_PATH="$TRANSCRIPT_DIR/parent.jsonl"
CHILD_PATH="$TRANSCRIPT_DIR/child.jsonl"
total=$(wc -l < "$PARENT_SRC")
head -n $((total - 1)) "$PARENT_SRC" > "$PARENT_PATH"
cp "$CHILD_SRC" "$CHILD_PATH"

echo "==> Workspace: $TMPDIR_BASE"
echo "==> Parent transcript: $(wc -l < "$PARENT_PATH") lines (task_complete stripped)"

# Sanity check: the stripped file must NOT contain task_complete.
if grep -q 'task_complete' "$PARENT_PATH"; then
    echo "ERROR: parent.jsonl still contains task_complete — head -n strip failed" >&2
    exit 1
fi

# Fire Stop while the turn is still open. It must remain pending.
echo "==> [Stop] sid=$PARENT_SID path=parent.jsonl (must remain pending)"
printf '{"hook_event_name":"Stop","session_id":"%s","transcript_path":"%s"}\n' \
    "$PARENT_SID" "$PARENT_PATH" | "$BIN" >/dev/null 2>&1 || true

# The hook parent already spawned the only Stop worker needed here. Do not
# queue a recovery nudge before appending task_complete: it would wait behind
# the first session owner, then legitimately observe the later append and race
# SessionEnd for the final export.
for _ in $(seq 1 250); do
    cat "$CDXHOOKS_LOG_DIR"/cdxhooks*.log > "$FIRST_LOG_FILE" 2>/dev/null || true
    if grep -q -E '"component":"worker".*"event":"Stop"' "$FIRST_LOG_FILE"; then
        break
    fi
    sleep 0.02
done
if ! grep -q -E '"component":"worker".*"event":"Stop"' "$FIRST_LOG_FILE"; then
    echo "ERROR: first Stop worker did not finish before SessionEnd recovery" >&2
    exit 1
fi

cat "$CDXHOOKS_LOG_DIR"/cdxhooks*.log > "$FIRST_LOG_FILE" 2>/dev/null || true
if [ -f "$REQUEST_COUNT" ]; then
    FIRST_REQUEST_COUNT=$(wc -l < "$REQUEST_COUNT" | tr -d ' ')
else
    FIRST_REQUEST_COUNT=0
fi
if [ "$FIRST_REQUEST_COUNT" -ne 0 ]; then
    echo "ERROR: open turn produced $FIRST_REQUEST_COUNT POST request(s)" >&2
    exit 1
fi
if grep -q -E '(Successfully posted|Failed to post session=)' "$FIRST_LOG_FILE"; then
    echo "ERROR: open turn attempted export during Stop" >&2
    exit 1
fi

# Append the authoritative terminal marker and use SessionEnd as the final
# compensation/drain event.
tail -n 1 "$PARENT_SRC" >> "$PARENT_PATH"
echo "==> [SessionEnd] task_complete appended; one export expected"
printf '{"hook_event_name":"SessionEnd","session_id":"%s","transcript_path":"%s"}\n' \
    "$PARENT_SID" "$PARENT_PATH" | "$BIN" >/dev/null 2>&1 || true
CDXHOOKS_WORKER_SESSION_ID="$PARENT_SID" \
    "$BIN" worker run --max-jobs 10 >/dev/null 2>&1 || true
wait "$SERVER_PID"
SERVER_PID=""

echo
echo "==> Log:"
# Hook writes flat per-session logs; merge them for assertions.
cat "$CDXHOOKS_LOG_DIR"/cdxhooks*.log > "$LOG_FILE" 2>/dev/null || true
cat "$LOG_FILE"

# State inspection
STATE_FILE="$(python3 - "$CDXHOOKS_STATE_DIR" "$PARENT_SID" <<'PY'
import json
import pathlib
import sys
import time

root = pathlib.Path(sys.argv[1])
for _ in range(250):
    matches = list(root.glob("*/state_stop.json"))
    if len(matches) == 1:
        print(matches[0])
        break
    time.sleep(0.02)
PY
)"
[ -n "$STATE_FILE" ] || { echo "ERROR: parent session state file not found" >&2; exit 1; }
TURN_COUNT=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    s = json.load(f)
print(s.get('turn_count', -1))
")
PENDING_LEN=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    s = json.load(f)
print(len(s.get('pending_messages', [])))
")

echo
echo "==> Checks"
fail=0
check_exact() {
    local label="$1"; local expected="$2"; local pattern="$3"
    local count; count=$(grep -c -E "$pattern" "$LOG_FILE" || true)
    if [ "$count" -eq "$expected" ]; then
        printf '   [OK]   %s (matched %d == %d)\n' "$label" "$count" "$expected"
    else
        printf '   [FAIL] %s (matched %d, expected %d) pattern=%s\n' "$label" "$count" "$expected" "$pattern"
        fail=1
    fi
}
check_at_least() {
    local label="$1"; local min="$2"; local pattern="$3"
    local count; count=$(grep -c -E "$pattern" "$LOG_FILE" || true)
    if [ "$count" -ge "$min" ]; then
        printf '   [OK]   %s (matched %d >= %d)\n' "$label" "$count" "$min"
    else
        printf '   [FAIL] %s (matched %d, expected >=%d) pattern=%s\n' "$label" "$count" "$min" "$pattern"
        fail=1
    fi
}

check_at_least "merged the sync sub-agent"     1 "Merging .* child session"
check_exact "exactly one successful finalizer export" 1 "Checkpointed session=$PARENT_SID turn_id="
check_at_least "SessionEnd compensation ran" 1 "\\[SessionEnd\\]"

if [ "$TURN_COUNT" -ge 1 ]; then
    printf '   [OK]   state.turn_count = %d (terminal turn exported)\n' "$TURN_COUNT"
else
    printf '   [FAIL] state.turn_count = %d, expected >=1\n' "$TURN_COUNT"
    fail=1
fi

if [ "$PENDING_LEN" -eq 0 ]; then
    printf '   [OK]   state.pending_messages drained\n'
else
    printf '   [FAIL] state.pending_messages = %d entries, expected 0\n' "$PENDING_LEN"
    fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
    test "$(wc -l < "$REQUEST_COUNT" | tr -d ' ')" -eq 1
    echo "==> PASS"
    rm -rf "$TMPDIR_BASE"
    exit 0
else
    echo "==> FAIL — log preserved at $LOG_FILE"
    exit 1
fi
