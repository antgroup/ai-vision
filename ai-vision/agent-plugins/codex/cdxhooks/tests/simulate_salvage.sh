#!/usr/bin/env bash
# simulate_salvage.sh
#
# Reproduces an interrupted session whose transcript becomes terminal later.
# A Stop for an unrelated session must not read, lock, or mutate the orphan's
# session-local state. A later SessionEnd for the owning session performs the
# authoritative recovery.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDXHOOKS_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

BIN="$CDXHOOKS_REPO/target/debug/cdxhooks"
PARENT_SRC="$SCRIPT_DIR/fixtures/codex_multi_agent_v2_parent.jsonl"
CHILD_SRC="$SCRIPT_DIR/fixtures/codex_multi_agent_v2_child.jsonl"

for f in "$BIN" "$PARENT_SRC" "$CHILD_SRC"; do
    [ -f "$f" ] || { echo "ERROR: missing $f" >&2; exit 1; }
done

PARENT_SID="parent-v2"
CHILD_SID="child-v2"
OTHER_SID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

TMPDIR_BASE="$(mktemp -d -t cdxhooks-salvage-XXXXXX)"
TRANSCRIPT_DIR="$TMPDIR_BASE/sessions"
mkdir -p "$TRANSCRIPT_DIR"
export CDXHOOKS_STATE_DIR="$TMPDIR_BASE/state"
export CDXHOOKS_SPOOL_DIR="$TMPDIR_BASE/spool"
export CDXHOOKS_LOG_DIR="$TMPDIR_BASE/logs"
mkdir -p "$CDXHOOKS_STATE_DIR" "$CDXHOOKS_SPOOL_DIR" "$CDXHOOKS_LOG_DIR"
LOG_FILE="$TMPDIR_BASE/merged.log"

export CDXHOOKS_LANGFUSE_BASE_URL="http://127.0.0.1:1"
export CDXHOOKS_LANGFUSE_PUBLIC_KEY="sim-pk"
export CDXHOOKS_LANGFUSE_SECRET_KEY="sim-sk"
export CDXHOOKS_TEST_DISABLE_FOREGROUND_WORKER=1
export CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS=750
export CDXHOOKS_STOP_BUDGET_MS=2000
export CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:1/v1/traces"
export NO_PROXY="127.0.0.1,localhost"

PARENT_PATH="$TRANSCRIPT_DIR/parent.jsonl"
CHILD_PATH="$TRANSCRIPT_DIR/child.jsonl"
OTHER_PATH="$TRANSCRIPT_DIR/unrelated.jsonl"

emit() {
    local sid="$1"; local path="$2"; local label="$3"
    echo "==> [$label] sid=$sid path=$(basename "$path")"
    printf '{"hook_event_name":"Stop","session_id":"%s","transcript_path":"%s"}\n' \
        "$sid" "$path" | "$BIN" >/dev/null 2>&1 || true
    CDXHOOKS_WORKER_SESSION_ID="$sid" \
        "$BIN" worker run --max-jobs 10 >/dev/null 2>&1 || true
}

echo "==> Workspace: $TMPDIR_BASE"

# --- Stage A: parent partial (no final_answer phase, no task_complete) ---
# To force a genuine orphan, strip BOTH parent final_answer records and
# everything after (task_complete etc). With the
# `turn_looks_complete` heuristic in place, this is the only way to prevent
# same-turn flush and exercise the recovery path. Lines 1-14 keep all tool_calls
# (including wait_agent + outputs) but omit the parent's terminal answer.
head -n 14 "$PARENT_SRC" > "$PARENT_PATH"
cp "$CHILD_SRC" "$CHILD_PATH"
emit "$PARENT_SID" "$PARENT_PATH" "Stage A: parent incomplete (no final_answer, no task_complete)"

# Verify orphan was created in exactly one session-local state file.
PARENT_STATE="$(python3 - "$CDXHOOKS_STATE_DIR" "$PARENT_SID" <<'PY'
import json
import pathlib
import sys
import time

root = pathlib.Path(sys.argv[1])
matches = []
for _ in range(250):
    matches = list(root.glob("*/state_stop.json"))
    if len(matches) == 1:
        break
    time.sleep(0.02)
if len(matches) != 1:
    raise SystemExit(f"expected one flat Stop domain, found {matches}")
print(matches[0])
PY
)"
ORPHAN_TURN_COUNT=$(python3 -c "
import json
with open('$PARENT_STATE') as f:
    s = json.load(f)
print(s.get('turn_count', -1))
")
if [ "$ORPHAN_TURN_COUNT" -le 0 ]; then
    echo "==> Verified: orphan state has no committed turn"
else
    echo "==> ERROR: expected no committed turn, got turn_count=$ORPHAN_TURN_COUNT"
    exit 1
fi

# --- Stage B: append final_answer agent_message + task_complete to parent file ---
# Simulates Codex finally writing the rest after a delay. This restores
# `phase=final_answer`, which the heuristic needs, plus `task_complete`.
sed -n '15,$p' "$PARENT_SRC" >> "$PARENT_PATH"
echo "==> Stage B: appended final_answer + task_complete to parent file"

# --- Stage C: unrelated Stop must not salvage or mutate the parent session ---
# Create an empty other-session transcript so the hook can create only the
# unrelated session's own runtime tree.
printf '{"type":"session_meta","payload":{"id":"%s"}}\n{"type":"event_msg","payload":{"type":"task_started","turn_id":"t1"}}\n' "$OTHER_SID" > "$OTHER_PATH"
emit "$OTHER_SID" "$OTHER_PATH" "Stage C: unrelated Stop remains session-isolated"

# The owning state must remain untouched.
AFTER_OTHER_TURN_COUNT=$(python3 -c "
import json
with open('$PARENT_STATE') as f:
    s = json.load(f)
print(s.get('turn_count', -1))
")
if [ "$AFTER_OTHER_TURN_COUNT" != "$ORPHAN_TURN_COUNT" ]; then
    echo "==> ERROR: unrelated Stop mutated parent state"
    exit 1
fi

# --- Stage D: the owning session performs authoritative recovery ---
printf '{"hook_event_name":"SessionEnd","session_id":"%s","transcript_path":"%s"}\n' \
    "$PARENT_SID" "$PARENT_PATH" | "$BIN" >/dev/null 2>&1 || true
CDXHOOKS_WORKER_SESSION_ID="$PARENT_SID" \
    "$BIN" worker run --max-jobs 10 >/dev/null 2>&1 || true

for _ in $(seq 1 1000); do
    if grep -q '"component":"worker".*"event":"SessionEnd"' \
        "$CDXHOOKS_LOG_DIR"/cdxhooks*.log 2>/dev/null; then
        break
    fi
    sleep 0.02
done

FINAL_TURN_COUNT=$(python3 -c "
import json
with open('$PARENT_STATE') as f:
    s = json.load(f)
print(s.get('turn_count', -1))
")

echo
echo "==> Log:"
# Hook writes flat per-session logs; merge them for assertions.
cat "$CDXHOOKS_LOG_DIR"/cdxhooks*.log > "$LOG_FILE" 2>/dev/null || true
cat "$LOG_FILE"

echo
echo "==> Checks"
fail=0
check() {
    local label="$1"; local pattern="$2"
    if grep -qE "$pattern" "$LOG_FILE"; then
        printf '   [OK]   %s\n' "$label"
    else
        printf '   [FAIL] %s  pattern=%s\n' "$label" "$pattern"
        fail=1
    fi
}

if grep -q "Salvage: completing orphan session=$PARENT_SID" "$LOG_FILE"; then
    echo "   [FAIL] unrelated session triggered cross-session salvage"
    fail=1
else
    echo "   [OK]   unrelated session did not trigger cross-session salvage"
fi
check "owning SessionEnd merged sync subagent" "Merging .* child session"
check "owning SessionEnd attempted post"       "Langfuse OTel HTTP request failed"
echo "   [INFO] Parent turn_count after owning recovery: $FINAL_TURN_COUNT"

echo
if [ "$fail" -eq 0 ]; then
    echo "==> PASS"
    rm -rf "$TMPDIR_BASE"
    exit 0
else
    echo "==> FAIL — log preserved at $LOG_FILE"
    exit 1
fi
