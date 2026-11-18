#!/bin/sh
# Both initialization and the serving process hold the same volume lock.
# Initialization must run on the dedicated internal network, without ports.
set -eu
mode=${1:?expected initialize, serve or health}
case "$mode" in
  initialize)
    # Bound the whole phase, including a server that accepts TCP but never
    # returns SQL. timeout signals the process group; cleanup has 90s to stop
    # the database before the final 120s kill allowance.
    exec timeout --signal=TERM --kill-after=120s 1200s /bin/sh "$0" initialize-bounded
    ;;
  initialize-bounded) mode=initialize;;
esac
sql_timeout=30s
sql_kill_after=5s
if [ "$mode" = health ]; then
  # Finish before the outer eight-second Docker health deadline, including
  # a SQL client that ignores TERM after accepting a connection.
  sql_timeout=3s
  sql_kill_after=1s
fi
mysql_command() {
  timeout --signal=TERM --kill-after="$sql_kill_after" "$sql_timeout" /usr/bin/mysql --no-defaults --protocol=TCP \
    --connect-timeout=5 -h127.0.0.1 -P2881 -uroot --batch --skip-column-names "$@"
}
sql() {
  MYSQL_PWD="$AIV_SEEKDB_PASSWORD" mysql_command "$@"
}
if [ "$mode" = health ]; then
  sql -e 'SELECT 1' >/dev/null
  exit
fi
case "$AIV_SEEKDB_PASSWORD" in
  ''|*[!a-zA-Z0-9_-]*) echo "seekdb requires a nonempty URL-safe password" >&2; exit 1;;
esac
cd /var/lib/seekdb
exec 9>.aiv-volume.lock
flock -n 9 || { echo "database volume is already in use; stop the database before initialization" >&2; exit 1; }
set -- /usr/bin/seekdb --nodaemon --base-dir=/var/lib/seekdb --port=2881 \
  --parameter "memory_budget=${AIV_SEEKDB_MEMORY_BUDGET:-${AIV_SEEKDB_MEMORY:-2G}}" \
  --parameter "cpu_count=${AIV_SEEKDB_CPU:-2}" \
  --parameter "datafile_size=${AIV_SEEKDB_DATA:-2G}" \
  --parameter "log_disk_size=${AIV_SEEKDB_LOG:-2G}"
if [ "$mode" = serve ]; then
  test -f .aiv-credentials-verified || {
    echo "run the isolated database-initialize service before starting seekdb" >&2; exit 1;
  }
fi
case "$mode" in initialize|serve) :;; *) echo "unknown seekdb lifecycle phase" >&2; exit 1;; esac
stop_database() {
  # seekdb's signal-wait thread treats TERM as an immediate SIGKILL.
  # USR1 invokes prepare_stop/set_stop; keep a supervising PID 1 so Docker's
  # TERM becomes the database's orderly shutdown signal, and verify exit 0.
  trap '' INT TERM
  kill -USR1 "$database_pid" 2>/dev/null || true
  shutdown_attempt=0
  while kill -0 "$database_pid" 2>/dev/null && [ "$shutdown_attempt" -lt 90 ]; do
    sleep 1
    shutdown_attempt=$((shutdown_attempt + 1))
  done
  forced=false
  if kill -0 "$database_pid" 2>/dev/null; then
    echo "seekdb exceeded its 90s shutdown allowance; forcing exit" >&2
    kill -KILL "$database_pid" 2>/dev/null || true
    forced=true
  fi
  database_exit=0
  wait "$database_pid" 2>/dev/null || database_exit=$?
  echo "seekdb stop: child_exit=$database_exit forced=$forced"
  [ "$forced" = false ] || return 137
  return "$database_exit"
}
if [ "$mode" = serve ]; then
  "$@" &
  database_pid=$!
  trap 'if stop_database; then exit 0; else exit $?; fi' INT TERM
  database_exit=0
  wait "$database_pid" || database_exit=$?
  echo "seekdb exited unexpectedly (exit $database_exit)" >&2
  [ "$database_exit" -ne 0 ] || database_exit=1
  exit "$database_exit"
fi
initializing=false
if [ -z "$(find . -mindepth 1 -maxdepth 1 ! -name .aiv-volume.lock -print -quit)" ]; then
  # Record ownership before the engine creates files. A failed first boot may
  # leave data files even though ALTER USER has not run yet.
  touch .aiv-initializing
fi
if [ -f .aiv-initializing ] && [ ! -f .aiv-credentials-verified ]; then initializing=true; fi
"$@" >/tmp/aiv-seekdb-startup.log 2>&1 &
database_pid=$!
cleanup() {
  phase_exit=$?
  trap - EXIT INT TERM
  stop_exit=0
  stop_database || stop_exit=$?
  [ "$phase_exit" -ne 0 ] || phase_exit=$stop_exit
  exit "$phase_exit"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ready=false
set_password=false
attempt=0
while [ "$attempt" -lt 200 ]; do
  kill -0 "$database_pid" 2>/dev/null || { echo "seekdb exited before readiness" >&2; exit 1; }
  if sql -e 'SELECT 1' >/tmp/aiv-seekdb-auth.log 2>&1; then
    ready=true; break
  elif grep -q 'ERROR 1045' /tmp/aiv-seekdb-auth.log; then
    if [ "$initializing" = true ] && MYSQL_PWD= mysql_command -e 'SELECT 1' >/dev/null 2>&1; then
      ready=true; set_password=true; break
    fi
    echo "existing seekdb volume rejected password; credentials were not changed" >&2; exit 1
  fi
  attempt=$((attempt + 1))
  sleep 3
done
[ "$ready" = true ] || { echo "seekdb readiness timed out" >&2; exit 1; }
if [ "$set_password" = true ]; then
  printf "ALTER USER 'root' IDENTIFIED BY '%s';\n" "$AIV_SEEKDB_PASSWORD" |
    MYSQL_PWD= mysql_command >/dev/null 2>&1
fi
# Retained Base64 and SQL framing exceed the 16 MiB media byte limit.
sql -e 'SET GLOBAL max_allowed_packet=67108864'
sql -e 'SELECT 1' >/dev/null 2>&1
if MYSQL_PWD= mysql_command -e 'SELECT 1' >/tmp/aiv-seekdb-empty.log 2>&1; then
  echo "seekdb still accepts an empty password" >&2; exit 1
fi
grep -q 'ERROR 1045' /tmp/aiv-seekdb-empty.log || { echo "could not verify empty password rejection" >&2; exit 1; }
touch .aiv-credentials-verified
rm -f .aiv-initializing
echo "seekdb credentials verified; existing data preserved"
