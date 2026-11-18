#!/bin/bash
# The artifact-only backend always provides the native management launcher.
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
cd /app
run_startup ./manage migrate --noinput
run_startup ./manage ensure_platform_config
run_startup ./manage seed_data
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec ./manage serve_with_projector --address "0.0.0.0:${BE_PORT:-8001}"
