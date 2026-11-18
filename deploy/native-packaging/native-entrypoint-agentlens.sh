#!/bin/bash
# Native schema/bootstrap dispatch preserves the self-hosted boot sequence.
set -euo pipefail
# Job control keeps SIGINT deliverable and gives each startup command a group.
set -m
startup_pid=
startup_launching=0
startup_signal=
startup_status=0
terminate_startup() {
  startup_signal="$1"
  startup_status="$2"
  # Defer a signal in the launch/PID-assignment window until the PID is known.
  if [ "$startup_launching" -eq 1 ]; then return; fi
  trap '' TERM INT
  if [ -n "$startup_pid" ]; then
    kill -s "$startup_signal" -- "-$startup_pid" 2>/dev/null || true
    wait "$startup_pid" || true
  fi
  exit "$startup_status"
}
trap 'terminate_startup TERM 143' TERM
trap 'terminate_startup INT 130' INT
run_startup() {
  startup_launching=1
  "$@" &
  startup_pid=$!
  startup_launching=0
  if [ -n "$startup_signal" ]; then
    terminate_startup "$startup_signal" "$startup_status"
  fi
  local status=0
  wait "$startup_pid" || status=$?
  startup_pid=
  return "$status"
}
org_id_output=
trap 'if [ -n "$org_id_output" ]; then rm -f "$org_id_output"; fi' EXIT
cd /app
if [ -z "${SIGNOZ_ORG_ID:-}" ] && [ -n "${SIGNOZ_ORG_ID_FILE:-}" ]; then
  # Keep the native reader in the parent shell's managed startup path.
  org_id_output="$(mktemp)"
  run_startup ./agentlens-migrate read-org-id-file > "$org_id_output"
  SIGNOZ_ORG_ID="$(< "$org_id_output")"
  rm -f "$org_id_output"
  org_id_output=
  export SIGNOZ_ORG_ID
fi
attempts="${AGENTLENS_MIGRATE_ATTEMPTS:-12}"
for i in $(seq 1 "$attempts"); do
  if run_startup ./agentlens-migrate -c obschema/alembic.ini upgrade head; then
    break
  fi
  if [ "$i" -eq "$attempts" ]; then
    echo "agentlens: migrations still failing after $attempts attempts" >&2
    exit 1
  fi
  echo "agentlens: migration attempt $i/$attempts failed (datastore not ready?), retrying in 5s" >&2
  run_startup sleep 5
done
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec python -m uvicorn --factory agentlens.main:build_app --host 0.0.0.0 --port "${AGENTLENS_PORT:-8081}"
