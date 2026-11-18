#!/usr/bin/env bash
# Manage Jaeger all-in-one binary for macOS arm64.
# This intentionally supports only Darwin arm64 artifacts for local agent testing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEAVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

JAEGER_HOME="${JAEGER_HOME:-$WEAVER_DIR/backends/jaeger}"
JAEGER_BIN_DIR="${JAEGER_BIN_DIR:-$JAEGER_HOME/bin}"
JAEGER_DOWNLOAD_DIR="${JAEGER_DOWNLOAD_DIR:-$JAEGER_HOME/downloads}"
JAEGER_LOG_DIR="${JAEGER_LOG_DIR:-$JAEGER_HOME/logs}"
JAEGER_PID_DIR="${JAEGER_PID_DIR:-$JAEGER_HOME/pids}"
JAEGER_VERSION="${JAEGER_VERSION:-v1.76.0}"
WEAVER_GUARD_OSS_PREFIX="${WEAVER_GUARD_OSS_PREFIX:-${AIVISION_GUARD_OSS_PREFIX:-}}"
JAEGER_UI_ADDR="${JAEGER_UI_ADDR:-127.0.0.1:16686}"
JAEGER_OTLP_HTTP_ADDR="${JAEGER_OTLP_HTTP_ADDR:-127.0.0.1:14318}"
JAEGER_OTLP_GRPC_ADDR="${JAEGER_OTLP_GRPC_ADDR:-127.0.0.1:14317}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") <command>

Commands:
  init        Create Jaeger backend directories
  download    Download Jaeger all-in-one macOS arm64 binary tarball
  verify      Verify local tarball or upstream release URL
  extract     Extract jaeger-all-in-one into JAEGER_HOME/bin
  install     init + download + extract
  start       Start local Jaeger all-in-one
  stop        Stop local Jaeger all-in-one
  restart     Stop then start local Jaeger all-in-one
  status      Print process status
  wait        Wait until Jaeger UI responds
  endpoints   Print OTLP and UI endpoint variables

Environment:
  JAEGER_HOME=$JAEGER_HOME
  JAEGER_VERSION=$JAEGER_VERSION
  WEAVER_GUARD_OSS_PREFIX=$WEAVER_GUARD_OSS_PREFIX
  JAEGER_UI_ADDR=$JAEGER_UI_ADDR
  JAEGER_OTLP_HTTP_ADDR=$JAEGER_OTLP_HTTP_ADDR
  JAEGER_OTLP_GRPC_ADDR=$JAEGER_OTLP_GRPC_ADDR
USAGE
}

log() { printf '[jaeger] %s\n' "$*"; }
fatal() { printf '[jaeger] ERROR: %s\n' "$*" >&2; exit 1; }

require_macos_arm64() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  if [ "$os" != "Darwin" ] || [ "$arch" != "arm64" ]; then
    fatal "only macOS arm64 is supported by this integration (detected ${os}/${arch})"
  fi
}

init_dirs() {
  mkdir -p "$JAEGER_BIN_DIR" "$JAEGER_DOWNLOAD_DIR" "$JAEGER_LOG_DIR" "$JAEGER_PID_DIR"
}

version_no_v() { printf '%s\n' "${JAEGER_VERSION#v}"; }
archive_name() { printf 'jaeger-all-in-one-%s-darwin-arm64.tar.gz\n' "$(version_no_v)"; }
archive_path() { printf '%s/%s\n' "$JAEGER_DOWNLOAD_DIR" "$(archive_name)"; }
binary_path() { printf '%s/jaeger-all-in-one\n' "$JAEGER_BIN_DIR"; }
pid_file() { printf '%s/jaeger-all-in-one.pid\n' "$JAEGER_PID_DIR"; }
log_file() { printf '%s/jaeger-all-in-one.log\n' "$JAEGER_LOG_DIR"; }
url() {
  [ -n "$WEAVER_GUARD_OSS_PREFIX" ] || fatal "Set WEAVER_GUARD_OSS_PREFIX to your asset host"
  printf '%s/backends/macos-arm64/jaeger/%s/%s\n' "${WEAVER_GUARD_OSS_PREFIX%/}" "$JAEGER_VERSION" "$(archive_name)"
}

is_positive_pid() {
  local pid="$1"
  case "$pid" in ''|*[!0-9]*) return 1 ;; *) [ "$pid" -gt 0 ] 2>/dev/null ;; esac
}

is_running_pid() {
  local pid="$1"
  is_positive_pid "$pid" && kill -0 "$pid" >/dev/null 2>&1
}

pid_matches_jaeger() {
  local pid="$1" command_line
  is_positive_pid "$pid" || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command_line" in *"jaeger-all-in-one"*) return 0 ;; *) return 1 ;; esac
}

is_running() {
  local file pid
  file="$(pid_file)"
  [ -f "$file" ] || return 1
  pid="$(cat "$file" 2>/dev/null || true)"
  is_running_pid "$pid" && pid_matches_jaeger "$pid"
}

download() {
  require_macos_arm64
  init_dirs
  local path tmp
  path="$(archive_path)"
  tmp="$path.tmp"
  if [ -s "$path" ]; then
    log "archive already exists: $path"
    return 0
  fi
  log "downloading $(url)"
  curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$(url)"
  mv "$tmp" "$path"
}

verify() {
  require_macos_arm64
  init_dirs
  local path
  path="$(archive_path)"
  if [ -s "$path" ]; then
    if tar -tzf "$path" | grep -Eq '(^|/)jaeger-all-in-one$'; then
      log "verified archive contains jaeger-all-in-one: $path"
      return 0
    fi
    fatal "archive does not contain jaeger-all-in-one: $path"
  fi
  log "verifying release URL: $(url)"
  curl -fsSIL --retry 3 --retry-delay 2 -o /dev/null "$(url)"
}

extract() {
  require_macos_arm64
  init_dirs
  local path tmp found dest
  path="$(archive_path)"
  [ -s "$path" ] || download
  tmp="$(mktemp -d "$JAEGER_HOME/extract.XXXXXX")"
  tar -xzf "$path" -C "$tmp"
  found="$(find "$tmp" -type f -name jaeger-all-in-one -print -quit)"
  if [ -z "$found" ]; then
    rm -rf "$tmp"
    fatal "archive $path did not contain jaeger-all-in-one"
  fi
  dest="$(binary_path)"
  cp "$found" "$dest"
  chmod +x "$dest"
  rm -rf "$tmp"
  log "installed binary: $dest"
}

start() {
  require_macos_arm64
  init_dirs
  local bin file logfile pid
  bin="$(binary_path)"
  [ -x "$bin" ] || extract
  if is_running; then
    log "already running (pid $(cat "$(pid_file)"))"
    return 0
  fi
  file="$(pid_file)"
  logfile="$(log_file)"
  log "starting jaeger-all-in-one ui=$JAEGER_UI_ADDR otlp_http=$JAEGER_OTLP_HTTP_ADDR log=$logfile"
  SPAN_STORAGE_TYPE=memory "$bin" \
    --query.http-server.host-port="$JAEGER_UI_ADDR" \
    --query.base-path=/jaeger \
    --collector.otlp.enabled=true \
    --collector.otlp.http.host-port="$JAEGER_OTLP_HTTP_ADDR" \
    --collector.otlp.grpc.host-port="$JAEGER_OTLP_GRPC_ADDR" \
    >>"$logfile" 2>&1 &
  pid="$!"
  printf '%s\n' "$pid" >"$file"
  sleep 0.2
  if ! is_running_pid "$pid"; then
    rm -f "$file"
    fatal "jaeger-all-in-one failed to start; see $logfile"
  fi
  log "started jaeger-all-in-one pid=$pid"
}

stop() {
  local file pid
  file="$(pid_file)"
  if [ ! -f "$file" ]; then
    log "not running (no pid file)"
    return 0
  fi
  pid="$(cat "$file" 2>/dev/null || true)"
  if is_running_pid "$pid" && pid_matches_jaeger "$pid"; then
    log "stopping jaeger-all-in-one pid=$pid"
    kill "$pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 40); do
      is_running_pid "$pid" || break
      sleep 0.1
    done
    is_running_pid "$pid" && kill -9 "$pid" >/dev/null 2>&1 || true
  else
    log "stale pid file: $file"
  fi
  rm -f "$file"
}

status() {
  if is_running; then
    printf 'jaeger-all-in-one running pid=%s ui=http://%s/jaeger/ otlp_http=http://%s/v1/traces\n' "$(cat "$(pid_file)")" "$JAEGER_UI_ADDR" "$JAEGER_OTLP_HTTP_ADDR"
  else
    printf 'jaeger-all-in-one stopped\n'
  fi
}

wait_ready() {
  local url_ui
  url_ui="http://$JAEGER_UI_ADDR/jaeger/"
  for _ in $(seq 1 120); do
    if curl -fsS "$url_ui" >/dev/null 2>&1; then
      log "ready: $url_ui"
      return 0
    fi
    sleep 0.25
  done
  fatal "timed out waiting for $url_ui"
}

endpoints() {
  cat <<ENDPOINTS
JAEGER_TRACES_ENDPOINT=http://$JAEGER_OTLP_HTTP_ADDR/v1/traces
JAEGER_UI=http://$JAEGER_UI_ADDR/jaeger/
ENDPOINTS
}

cmd="${1:-}"
case "$cmd" in
  init) require_macos_arm64; init_dirs ;;
  download) download ;;
  verify) verify ;;
  extract) extract ;;
  install) require_macos_arm64; init_dirs; download; extract ;;
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  wait) wait_ready ;;
  endpoints) endpoints ;;
  -h|--help|help|'') usage ;;
  *) fatal "unknown command '$cmd'" ;;
esac
