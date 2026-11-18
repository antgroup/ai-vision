#!/usr/bin/env bash
# Kill a real Stop process while request 4 is blocked, then verify restart
# continues from the first uncheckpointed turn.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN="$REPO/target/debug/cdxhooks"
SINK="$SCRIPT_DIR/progressive_stop_sink.py"

for file in "$BIN" "$SINK"; do
    [ -f "$file" ] || { echo "ERROR: missing $file" >&2; exit 1; }
done

WORK="$(mktemp -d -t cdxhooks-checkpoint-XXXXXX)"
trap 'kill "${HOOK_PID:-}" "${SINK_PID:-}" 2>/dev/null || true; rm -rf "$WORK"' EXIT
mkdir -p "$WORK/state" "$WORK/spool" "$WORK/logs" "$WORK/sessions"

export CDXHOOKS_STATE_DIR="$WORK/state"
export CDXHOOKS_SPOOL_DIR="$WORK/spool"
export CDXHOOKS_LOG_DIR="$WORK/logs"
export CODEX_SESSIONS_DIR="$WORK/sessions"
export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export CDXHOOKS_STOP_BUDGET_MS=14000
export CDXHOOKS_STOP_HTTP_TIMEOUT_MS=5000
export CDXHOOKS_STOP_CHECKPOINT_RESERVE_MS=500
export CDXHOOKS_TEST_DISABLE_FOREGROUND_WORKER=1
export CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS=750
export CDXHOOKS_INTERNAL_WORKER=true

TRANSCRIPT="$WORK/sessions/parent.jsonl"
printf '%s\n' '{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}' > "$TRANSCRIPT"
for turn in 1 2 3 4 5; do
    printf '{"timestamp":"2026-07-13T00:00:%02dZ","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-%d"}}\n' "$turn" "$turn" >> "$TRANSCRIPT"
    printf '{"timestamp":"2026-07-13T00:00:%02d.100Z","type":"event_msg","payload":{"type":"user_message","message":"request-%d"}}\n' "$turn" "$turn" >> "$TRANSCRIPT"
    printf '{"timestamp":"2026-07-13T00:00:%02d.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"response-%d"}}\n' "$turn" "$turn" >> "$TRANSCRIPT"
    printf '{"timestamp":"2026-07-13T00:00:%02d.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-%d","last_agent_message":"response-%d"}}\n' "$turn" "$turn" "$turn" >> "$TRANSCRIPT"
done

PAYLOAD="$WORK/payload.json"
printf '{"hook_event_name":"Stop","session_id":"parent","transcript_path":"%s"}\n' "$TRANSCRIPT" > "$PAYLOAD"
STATE=""

free_port() {
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

wait_for_file() {
    local path="$1"
    for _ in $(seq 1 750); do
        [ -f "$path" ] && return 0
        sleep 0.02
    done
    echo "ERROR: timed out waiting for $path" >&2
    cat "$WORK"/logs/cdxhooks*.log 2>/dev/null || true
    cat "$STATE" 2>/dev/null || true
    wc -l "$FIRST_REQUESTS" 2>/dev/null || true
    return 1
}

PORT1="$(free_port)"
FIRST_REQUESTS="$WORK/first.jsonl"
BLOCKED="$WORK/fourth-blocked"
python3 "$SINK" "$PORT1" "$FIRST_REQUESTS" 3 1 "$BLOCKED" 2>"$WORK/sink1.err" &
SINK_PID=$!
sleep 0.1
kill -0 "$SINK_PID"
export CDXHOOKS_LANGFUSE_BASE_URL="http://127.0.0.1:$PORT1"
export CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:$PORT1/v1/traces"
"$BIN" __worker < "$PAYLOAD" >/dev/null 2>&1 &
HOOK_PID=$!

wait_for_file "$BLOCKED"
STATE="$(find "$WORK/state" -name state_stop.json -type f | head -n 1)"
[ -n "$STATE" ] || { echo "ERROR: session-local state file not found" >&2; exit 1; }
python3 - "$STATE" "$TRANSCRIPT" <<'PY'
import json, os, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["offset"] == 0, state
assert state["turn_count"] == 3, state
assert state["posted_turn_ids"] == ["turn-1", "turn-2", "turn-3"], state
assert state["pending_export_reason"] == "partial_export_in_progress", state
PY

kill -TERM "$HOOK_PID"
wait "$HOOK_PID" 2>/dev/null || true
HOOK_PID=""
kill -TERM "$SINK_PID"
wait "$SINK_PID" 2>/dev/null || true
SINK_PID=""

PORT2="$(free_port)"
SECOND_REQUESTS="$WORK/second.jsonl"
python3 "$SINK" "$PORT2" "$SECOND_REQUESTS" 2 0 "$WORK/unused" 2>"$WORK/sink2.err" &
SINK_PID=$!
sleep 0.1
kill -0 "$SINK_PID"
export CDXHOOKS_LANGFUSE_BASE_URL="http://127.0.0.1:$PORT2"
export CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:$PORT2/v1/traces"
"$BIN" __worker < "$PAYLOAD" >/dev/null 2>&1
wait "$SINK_PID"
SINK_PID=""

python3 - "$TRANSCRIPT" "$STATE" "$FIRST_REQUESTS" "$SECOND_REQUESTS" <<'PY'
import json, os, sys
transcript, state_path, first_path, second_path = sys.argv[1:]
state = json.load(open(state_path, encoding="utf-8"))
assert state["offset"] == os.path.getsize(transcript), state
assert state["turn_count"] == 5, state
assert state["posted_turn_ids"] == [f"turn-{i}" for i in range(1, 6)], state
assert "pending_export_reason" not in state, state

first = [json.loads(line) for line in open(first_path, encoding="utf-8")]
second = [json.loads(line) for line in open(second_path, encoding="utf-8")]
assert len(first) == 4, len(first)
assert len(second) == 2, len(second)

def root_names(requests):
    names = []
    for request in requests:
        spans = request["resourceSpans"][0]["scopeSpans"][0]["spans"]
        names.append(next(span["name"] for span in spans if span["name"].startswith("Turn ")))
    return names

assert root_names(first[:3]) == ["Turn 1", "Turn 2", "Turn 3"]
assert root_names(second) == ["Turn 4", "Turn 5"]
print("PASS: hard kill preserved three checkpoints; restart sent only turns 4-5")
PY
