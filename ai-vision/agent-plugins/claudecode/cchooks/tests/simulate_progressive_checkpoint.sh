#!/usr/bin/env bash
# Black-box regression for cchooks progressive per-turn checkpoint delivery.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN="${CCHOOKS_BIN:-$REPO/target/debug/cchooks}"
SINK="$SCRIPT_DIR/progressive_checkpoint_sink.py"

for file in "$BIN" "$SINK"; do
    [ -f "$file" ] || { echo "ERROR: missing $file" >&2; exit 1; }
done

WORK="$(mktemp -d -t cchooks-progressive-XXXXXX)"
trap 'kill "${SINK_PID:-}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export CCHOOKS_ENABLE_TELEMETRY=true
export CCHOOKS_LANGFUSE_PUBLIC_KEY="test-pk"
export CCHOOKS_LANGFUSE_SECRET_KEY="test-sk"
export CCHOOKS_DEBUG=true
export CCHOOKS_RETRY_POLICY=at_least_once

free_port() {
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

wait_for_file() {
    local path="$1"
    for _ in $(seq 1 250); do
        [ -f "$path" ] && return 0
        sleep 0.02
    done
    echo "ERROR: timed out waiting for $path" >&2
    return 1
}

start_sink() {
    local scenario="$1" statuses="$2"
    stop_sink
    PORT="$(free_port)"
    REQUESTS="$WORK/$scenario.requests.jsonl"
    READY="$WORK/$scenario.ready"
    python3 "$SINK" "$PORT" "$REQUESTS" "$READY" "$statuses" \
        2>"$WORK/$scenario.sink.log" &
    SINK_PID=$!
    wait_for_file "$READY"
    kill -0 "$SINK_PID"
    export CCHOOKS_LANGFUSE_BASE_URL="http://127.0.0.1:$PORT"
    export CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:$PORT/v1/traces"
    export CCHOOKS_METRICS_ENDPOINT="http://127.0.0.1:$PORT/metrics"
}

stop_sink() {
    if [ -n "${SINK_PID:-}" ]; then
        kill "$SINK_PID" 2>/dev/null || true
        wait "$SINK_PID" 2>/dev/null || true
        SINK_PID=""
    fi
}

new_scenario() {
    local name="$1"
    unset CCHOOKS_STOP_BUDGET_MS
    unset CCHOOKS_SESSION_END_BUDGET_MS
    unset CCHOOKS_STOP_CHECKPOINT_RESERVE_MS
    export CCHOOKS_RETRY_POLICY=at_least_once
    SCENARIO="$WORK/$name"
    mkdir -p "$SCENARIO/state" "$SCENARIO/logs" "$SCENARIO/replays"
    export CCHOOKS_STATE_DIR="$SCENARIO/state"
    export CCHOOKS_LOG_DIR="$SCENARIO/logs"
    export CCHOOKS_REPLAY_DIR="$SCENARIO/replays"
    TRANSCRIPT="$SCENARIO/transcript.jsonl"
    SESSION_ID="$name-session"
}

# cchooks persists per-session state at $STATE_DIR/sessions/<session>-<hash>/state.json
# (the JSON is still keyed by <session> inside). Resolve the actual file per scenario.
state_path() {
    local found
    found="$(ls "$SCENARIO/state/sessions/${SESSION_ID}"-*/state.json 2>/dev/null | head -1)"
    [ -n "$found" ] || {
        echo "ERROR: no state.json for $SESSION_ID under $SCENARIO/state" >&2
        return 1
    }
    printf '%s' "$found"
}

# Lifecycle log contract: every physical live attempt emits exactly one
# langfuse_send_start and one terminal langfuse_send_end with a matching
# attempt_id, and no lifecycle line leaks the endpoint/host.
assert_lifecycle_pairs() {
    python3 - "$SCENARIO/logs" <<'PY'
import glob, os, re, sys
text = ""
for f in glob.glob(os.path.join(sys.argv[1], "*")):
    try:
        text += open(f, encoding="utf-8", errors="replace").read()
    except OSError:
        pass
starts, ends = [], []
for line in text.splitlines():
    if "event=langfuse_send_start" in line:
        m = re.search(r"attempt_id=(\S+)", line)
        if m:
            starts.append(m.group(1))
    elif "event=langfuse_send_end" in line:
        m = re.search(r"attempt_id=(\S+)", line)
        if m:
            ends.append(m.group(1))
assert len(starts) == len(ends), (
    f"start/end count mismatch: {len(starts)} starts vs {len(ends)} ends\n{text}"
)
assert set(starts) == set(ends), (
    f"attempt_id sets differ: starts={starts} ends={ends}"
)
assert len(starts) >= 1, f"expected at least one lifecycle pair, got 0\n{text}"
for line in text.splitlines():
    if "event=langfuse_send_" in line:
        assert "127.0.0.1" not in line and "/v1/traces" not in line, (
            f"endpoint/host leaked into lifecycle line: {line}"
        )
PY
}

append_complete_turn() {
    local transcript="$1" number="$2"
    printf '{"type":"user","uuid":"turn-%s","message":{"role":"user","content":"request-%s"},"timestamp":"2026-07-13T00:00:%02d.000Z"}\n' \
        "$number" "$number" "$number" >> "$transcript"
    printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"response-%s"}],"id":"msg-%s","model":"claude-sonnet-4-20250514","stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":3}},"timestamp":"2026-07-13T00:00:%02d.500Z"}\n' \
        "$number" "$number" "$number" >> "$transcript"
}

run_hook() {
    local event="$1"
    printf '{"hook_event_name":"%s","session_id":"%s","transcript_path":"%s","cwd":"%s"}\n' \
        "$event" "$SESSION_ID" "$TRANSCRIPT" "$REPO" | "$BIN" >/dev/null
}

assert_request_turns() {
    local request_path="$1" expected_csv="$2"
    python3 - "$request_path" "$expected_csv" <<'PY'
import json, sys
path, expected_csv = sys.argv[1:]
records = [json.loads(line) for line in open(path, encoding="utf-8")]
actual = []
for record in records:
    spans = record["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    roots = [span["name"] for span in spans if span["name"].startswith("Turn ")]
    assert len(roots) == 1, (record, roots)
    actual.append(roots[0])
expected = expected_csv.split(",") if expected_csv else []
assert actual == expected, (actual, expected)
PY
}

echo "==> Scenario 1: three ordered POSTs and final cursor commit"
new_scenario ordered
: > "$TRANSCRIPT"
for turn in 1 2 3; do append_complete_turn "$TRANSCRIPT" "$turn"; done
start_sink ordered "200"
run_hook Stop
stop_sink
assert_request_turns "$REQUESTS" "Turn 1,Turn 2,Turn 3"
python3 - "$(state_path)" "$TRANSCRIPT" <<'PY'
import json, os, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["ordered-session"]
assert state["offset"] == os.path.getsize(sys.argv[2]), state
assert state["turn_count"] == 3, state
assert not state.get("posted_turn_ids"), state
assert "pending_export_reason" not in state, state
PY

assert_lifecycle_pairs

echo "==> Scenario 2: at-least-once failure is compensated by SessionEnd"
new_scenario restart
: > "$TRANSCRIPT"
for turn in 1 2 3; do append_complete_turn "$TRANSCRIPT" "$turn"; done
start_sink restart_first "200,500"
run_hook Stop
stop_sink
assert_request_turns "$REQUESTS" "Turn 1,Turn 2"
python3 - "$(state_path)" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["restart-session"]
assert state["offset"] == 0, state
assert state["turn_count"] == 1, state
assert state["posted_turn_ids"] == ["turn-1"], state
assert state["pending_export_reason"] == "otel_export_failed", state
PY
start_sink restart_second "204"
run_hook SessionEnd
stop_sink
assert_request_turns "$REQUESTS" "Turn 2,Turn 3"
python3 - "$(state_path)" "$TRANSCRIPT" <<'PY'
import json, os, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["restart-session"]
assert state["offset"] == os.path.getsize(sys.argv[2]), state
assert state["turn_count"] == 3, state
assert not state.get("posted_turn_ids"), state
assert "pending_export_reason" not in state, state
PY

assert_lifecycle_pairs

echo "==> Scenario 3: 302 is not followed and cannot checkpoint"
new_scenario redirect
: > "$TRANSCRIPT"
append_complete_turn "$TRANSCRIPT" 1
start_sink redirect "302"
run_hook Stop
stop_sink
python3 - "$REQUESTS" "$(state_path)" <<'PY'
import json, sys
records = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
assert [(r["method"], r["path"], r["status"]) for r in records] == [
    ("POST", "/v1/traces", 302)
], records
state = json.load(open(sys.argv[2], encoding="utf-8"))["redirect-session"]
assert state.get("offset", 0) == 0, state
assert state.get("turn_count", 0) == 0, state
assert not state.get("posted_turn_ids"), state
assert state["pending_export_reason"] == "otel_export_failed", state
PY

assert_lifecycle_pairs

echo "==> Scenario 4: 400 cannot checkpoint"
new_scenario bad_request
: > "$TRANSCRIPT"
append_complete_turn "$TRANSCRIPT" 1
start_sink bad_request "400"
run_hook Stop
stop_sink
python3 - "$REQUESTS" "$(state_path)" <<'PY'
import json, sys
records = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
assert len(records) == 1 and records[0]["status"] == 400, records
state = json.load(open(sys.argv[2], encoding="utf-8"))["bad_request-session"]
assert state.get("offset", 0) == 0, state
assert state.get("turn_count", 0) == 0, state
assert not state.get("posted_turn_ids"), state
assert state["pending_export_reason"] == "otel_export_failed", state
PY

assert_lifecycle_pairs

echo "==> Scenario 5: incomplete tail retains cursor, then restart sends only tail"
new_scenario incomplete
: > "$TRANSCRIPT"
append_complete_turn "$TRANSCRIPT" 1
append_complete_turn "$TRANSCRIPT" 2
printf '%s\n' '{"type":"user","uuid":"turn-3","message":{"role":"user","content":"request-3"},"timestamp":"2026-07-13T00:00:03.000Z"}' >> "$TRANSCRIPT"
start_sink incomplete_first "200"
run_hook Stop
stop_sink
assert_request_turns "$REQUESTS" "Turn 1,Turn 2"
python3 - "$(state_path)" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["incomplete-session"]
assert state["offset"] == 0, state
assert state["turn_count"] == 2, state
assert state["posted_turn_ids"] == ["turn-1", "turn-2"], state
assert state["pending_export_reason"] == "incomplete_tail", state
PY
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"response-3"}],"id":"msg-3","model":"claude-sonnet-4-20250514","stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":3}},"timestamp":"2026-07-13T00:00:03.500Z"}' >> "$TRANSCRIPT"
start_sink incomplete_second "200"
run_hook Stop
stop_sink
assert_request_turns "$REQUESTS" "Turn 3"
python3 - "$(state_path)" "$TRANSCRIPT" <<'PY'
import json, os, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["incomplete-session"]
assert state["offset"] == os.path.getsize(sys.argv[2]), state
assert state["turn_count"] == 3, state
assert not state.get("posted_turn_ids"), state
assert "pending_export_reason" not in state, state
PY

assert_lifecycle_pairs

echo "==> Scenario 6: SessionEnd uses its longer budget and has no three-turn cap"
new_scenario session_end
: > "$TRANSCRIPT"
for turn in 1 2 3 4 5; do append_complete_turn "$TRANSCRIPT" "$turn"; done
export CCHOOKS_STOP_BUDGET_MS=1
export CCHOOKS_SESSION_END_BUDGET_MS=5000
export CCHOOKS_STOP_CHECKPOINT_RESERVE_MS=100
start_sink session_end "299"
run_hook Stop
[ ! -s "$REQUESTS" ] || {
    echo "ERROR: 1ms Stop budget unexpectedly sent a request" >&2
    exit 1
}
run_hook SessionEnd
stop_sink
assert_request_turns "$REQUESTS" "Turn 1,Turn 2,Turn 3,Turn 4,Turn 5"
python3 - "$(state_path)" "$TRANSCRIPT" <<'PY'
import json, os, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["session_end-session"]
assert state["offset"] == os.path.getsize(sys.argv[2]), state
assert state["turn_count"] == 5, state
assert not state.get("posted_turn_ids"), state
assert "pending_export_reason" not in state, state
PY

assert_lifecycle_pairs

echo "==> Scenario 7: at-most-once failure is checkpointed before POST and not retried"
new_scenario at_most_once
export CCHOOKS_RETRY_POLICY=at_most_once
: > "$TRANSCRIPT"
append_complete_turn "$TRANSCRIPT" 1
start_sink at_most_once "500,200"
run_hook Stop
run_hook SessionEnd
stop_sink
assert_request_turns "$REQUESTS" "Turn 1"
python3 - "$(state_path)" "$TRANSCRIPT" <<'PY'
import json, os, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))["at_most_once-session"]
assert state["offset"] == os.path.getsize(sys.argv[2]), state
assert state["turn_count"] == 1, state
assert not state.get("posted_turn_ids"), state
assert "pending_export_reason" not in state, state
PY

assert_lifecycle_pairs

echo "PASS: cchooks progressive checkpoint integration scenarios"
