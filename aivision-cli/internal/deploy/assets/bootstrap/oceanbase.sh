#!/bin/sh
# Preserve the upstream CE bootstrap, but own TERM and verify database shutdown.
# This lifecycle supports the standard obcluster layout, not upstream SLIM/demo.
set -eu

# pgrep includes zombies. Inspect the kernel state without signalling the
# observer; a failed query/read must not be mistaken for a stopped database.
observer_states() {
  if pids=$(pgrep -x observer); then
    test -n "$pids" || { echo "OceanBase observer query returned no PIDs" >&2; return 2; }
  else
    query=$?
    if [ "$query" -eq 1 ] && [ -z "$pids" ]; then return 0; fi
    echo "OceanBase observer query failed (pgrep exit $query)" >&2
    return 2
  fi
  for pid in $pids; do
    case "$pid" in ''|*[!0-9]*) echo "OceanBase observer query returned an invalid PID" >&2; return 2;; esac
    test "$pid" -gt 0 || { echo "OceanBase observer query returned an invalid PID" >&2; return 2; }
    if comm=$(cat "/proc/$pid/comm" 2>/dev/null); then :; else
      test ! -d "/proc/$pid" && continue
      echo "OceanBase observer state unknown: pid=$pid comm read failed" >&2; return 2
    fi
    if [ "$comm" != observer ]; then
      test ! -d "/proc/$pid" && continue
      echo "OceanBase observer state unknown: pid=$pid comm changed or empty" >&2; return 2
    fi
    if status=$(cat "/proc/$pid/status" 2>/dev/null); then :; else
      test ! -d "/proc/$pid" && continue
      echo "OceanBase observer state unknown: pid=$pid status read failed" >&2; return 2
    fi
    state=
    while read -r key value rest; do
      if [ "$key" = State: ]; then state=$value; break; fi
    done <<EOF
$status
EOF
    case "$state" in
      Z|X|x) printf '%s:%s:dead ' "$pid" "$state";;
      R|S|D|T|t|W|K|P|I) printf '%s:%s:live ' "$pid" "$state";;
      *)
        test ! -d "/proc/$pid" && continue
        echo "OceanBase observer state unknown: pid=$pid state=${state:-missing}" >&2
        return 2;;
    esac
  done
}

verify_observer_exit() {
  previous=
  while :; do
    if states=$(observer_states); then :; else return $?; fi
    if [ "$states" != "$previous" ]; then
      echo "OceanBase observer verification: ${states:-none}" >&2
      previous=$states
    fi
    case "$states" in *:live*) sleep 0.05;; *) return 0;; esac
  done
}

# The timeout child performs read-only verification, never sshd/boot reentry.
if [ "${1-}" = __verify_observer_exit ]; then
  if verify_observer_exit; then exit 0; else exit $?; fi
fi

# Recast can unlink or atomically replace the mounted script before shutdown.
# Pin this running version so its bounded verifier does not reopen that path.
lifecycle_source=$(cat "$0")

case "${MODE:-mini}" in
  [Ss][Ll][Ii][Mm]) echo "managed OceanBase lifecycle requires the standard obcluster layout; SLIM/demo is unsupported" >&2; exit 1;;
esac
for executable in /usr/sbin/sshd /root/boot/start.sh /usr/bin/obd; do
  test -x "$executable" || { echo "OceanBase image lacks required lifecycle executable: $executable" >&2; exit 1; }
done
for executable in setsid timeout pgrep; do
  command -v "$executable" >/dev/null || { echo "OceanBase image lacks required lifecycle tool: $executable" >&2; exit 1; }
done
cd /root
test -r boot/env.sh || { echo "OceanBase image lacks boot/env.sh" >&2; exit 1; }
# The single-node engine persists its advertised address inside its data.
# A Docker network can receive another subnet after down/up. Use loopback for
# new clusters; the SQL listener remains reachable through the container network.
# Existing OBD configuration and database addresses must never be rewritten.
if [ ! -f /root/.obd/cluster/obcluster/config.yaml ]; then
  export OB_SERVER_IP="${OB_SERVER_IP:-127.0.0.1}"
fi
/usr/sbin/sshd
# An independent process group lets us stop bootstrap SQL/OBD subprocesses
# before issuing stop. The observer daemon has its own session.
setsid /root/boot/start.sh &
boot_pid=$!
shutdown() {
  trap '' INT TERM
  kill -TERM "-$boot_pid" 2>/dev/null || true
  attempt=0
  while kill -0 "$boot_pid" 2>/dev/null && [ "$attempt" -lt 5 ]; do
    sleep 1
    attempt=$((attempt + 1))
  done
  if kill -0 "$boot_pid" 2>/dev/null; then
    echo "OceanBase bootstrap did not stop within 5s; terminating its process group" >&2
    kill -KILL "-$boot_pid" 2>/dev/null || true
  fi
  wait "$boot_pid" 2>/dev/null || true
  if [ -f /root/.obd/cluster/obcluster/config.yaml ]; then
    # 5s bootstrap + 100s OBD + 5s OBD kill + 5s verification fits the 120s grace.
    if timeout --signal=TERM --kill-after=5s 100s /usr/bin/obd cluster stop obcluster; then
      :
    else
      code=$?
      echo "OceanBase cluster stop failed or timed out (exit $code)" >&2
      return "$code"
    fi
  fi
  # TERM is ignored during shutdown and inherited by children. KILL bounds only
  # this independent verifier group; observer is never signalled here. Keep all
  # queries/diagnostics inside the deadline, including failures to read /proc.
  if timeout --signal=KILL 5s /bin/sh -c "$lifecycle_source" "$0" __verify_observer_exit; then
    :
  else
    code=$?
    echo "OceanBase observer verification failed or exceeded 5s (exit $code); see PID/state diagnostics above" >&2
    return "$code"
  fi
  echo "OceanBase cluster stopped"
}
trap 'if shutdown; then exit 0; else exit $?; fi' INT TERM
if wait "$boot_pid"; then
  code=1
  echo "OceanBase bootstrap exited unexpectedly" >&2
else
  code=$?
  echo "OceanBase bootstrap failed (exit $code)" >&2
fi
# A failed bootstrap may already have started the database.
if shutdown; then :; else exit $?; fi
exit "$code"
