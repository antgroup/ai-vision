#!/usr/bin/env bash
# Manage VictoriaMetrics/VictoriaTraces/VictoriaLogs binaries for macOS arm64.
# This intentionally supports only Darwin arm64 artifacts for local agent testing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEAVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VICTORIA_HOME="${VICTORIA_HOME:-$WEAVER_DIR/backends/victoria}"
VICTORIA_BIN_DIR="${VICTORIA_BIN_DIR:-$VICTORIA_HOME/bin}"
VICTORIA_DOWNLOAD_DIR="${VICTORIA_DOWNLOAD_DIR:-$VICTORIA_HOME/downloads}"
VICTORIA_DATA_DIR="${VICTORIA_DATA_DIR:-$VICTORIA_HOME/data}"
VICTORIA_LOG_DIR="${VICTORIA_LOG_DIR:-$VICTORIA_HOME/logs}"
VICTORIA_PID_DIR="${VICTORIA_PID_DIR:-$VICTORIA_HOME/pids}"

VM_VERSION="${VM_VERSION:-v1.147.0}"
VL_VERSION="${VL_VERSION:-v1.51.0}"
VT_VERSION="${VT_VERSION:-v0.9.3}"
WEAVER_GUARD_OSS_PREFIX="${WEAVER_GUARD_OSS_PREFIX:-${AIVISION_GUARD_OSS_PREFIX:-}}"

VICTORIA_METRICS_ADDR="${VICTORIA_METRICS_ADDR:-127.0.0.1:8428}"
VICTORIA_TRACES_ADDR="${VICTORIA_TRACES_ADDR:-127.0.0.1:10428}"
VICTORIA_LOGS_ADDR="${VICTORIA_LOGS_ADDR:-127.0.0.1:9428}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") <command> [component]

Commands:
  init                 Create Victoria backend directories
  download [all|...]   Download macOS arm64 release tarballs
  verify [all|...]     Verify local tarballs or upstream release URLs
  extract [all|...]    Extract binaries into VICTORIA_HOME/bin
  install [all|...]    init + download + extract
  start [all|...]      Start local Victoria services
  stop [all|...]       Stop local Victoria services
  restart [all|...]    Stop then start local Victoria services
  status [all|...]     Print process status
  wait [all|...]       Wait until HTTP health endpoints respond
  endpoints            Print OTLP and UI endpoint variables

Components:
  all, metrics, traces, logs

Environment:
  VICTORIA_HOME=$VICTORIA_HOME
  VM_VERSION=$VM_VERSION
  VL_VERSION=$VL_VERSION
  VT_VERSION=$VT_VERSION
  WEAVER_GUARD_OSS_PREFIX=$WEAVER_GUARD_OSS_PREFIX
  VICTORIA_METRICS_ADDR=$VICTORIA_METRICS_ADDR
  VICTORIA_TRACES_ADDR=$VICTORIA_TRACES_ADDR
  VICTORIA_LOGS_ADDR=$VICTORIA_LOGS_ADDR
USAGE
}

log() {
  printf '[victoria] %s\n' "$*"
}

fatal() {
  printf '[victoria] ERROR: %s\n' "$*" >&2
  exit 1
}

require_macos_arm64() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  if [ "$os" != "Darwin" ] || [ "$arch" != "arm64" ]; then
    fatal "only macOS arm64 is supported by this integration (detected ${os}/${arch})"
  fi
}

init_dirs() {
  mkdir -p "$VICTORIA_BIN_DIR" "$VICTORIA_DOWNLOAD_DIR" "$VICTORIA_DATA_DIR" "$VICTORIA_LOG_DIR" "$VICTORIA_PID_DIR"
}

components_for() {
  local requested="${1:-all}"
  case "$requested" in
    all) printf '%s\n' metrics traces logs ;;
    metrics|traces|logs) printf '%s\n' "$requested" ;;
    *) fatal "unknown component '$requested' (expected all|metrics|traces|logs)" ;;
  esac
}

version_for() {
  case "$1" in
    metrics) printf '%s\n' "$VM_VERSION" ;;
    traces) printf '%s\n' "$VT_VERSION" ;;
    logs) printf '%s\n' "$VL_VERSION" ;;
    *) fatal "unknown component '$1'" ;;
  esac
}

archive_name_for() {
  local component="$1" version
  version="$(version_for "$component")"
  case "$component" in
    metrics) printf 'victoria-metrics-darwin-arm64-%s.tar.gz\n' "$version" ;;
    traces) printf 'victoria-traces-darwin-arm64-%s.tar.gz\n' "$version" ;;
    logs) printf 'victoria-logs-darwin-arm64-%s.tar.gz\n' "$version" ;;
    *) fatal "unknown component '$component'" ;;
  esac
}

url_for() {
  [ -n "$WEAVER_GUARD_OSS_PREFIX" ] || fatal "Set WEAVER_GUARD_OSS_PREFIX to your asset host"
  local component="$1" version archive
  version="$(version_for "$component")"
  archive="$(archive_name_for "$component")"
  case "$component" in
    metrics) printf '%s/backends/macos-arm64/vmetrics/%s/%s\n' "${WEAVER_GUARD_OSS_PREFIX%/}" "$version" "$archive" ;;
    traces) printf '%s/backends/macos-arm64/vtraces/%s/%s\n' "${WEAVER_GUARD_OSS_PREFIX%/}" "$version" "$archive" ;;
    logs) printf '%s/backends/macos-arm64/vlogs/%s/%s\n' "${WEAVER_GUARD_OSS_PREFIX%/}" "$version" "$archive" ;;
    *) fatal "unknown component '$component'" ;;
  esac
}

binary_name_for() {
  case "$1" in
    metrics) printf 'victoria-metrics-prod\n' ;;
    traces) printf 'victoria-traces-prod\n' ;;
    logs) printf 'victoria-logs-prod\n' ;;
    *) fatal "unknown component '$1'" ;;
  esac
}

addr_for() {
  case "$1" in
    metrics) printf '%s\n' "$VICTORIA_METRICS_ADDR" ;;
    traces) printf '%s\n' "$VICTORIA_TRACES_ADDR" ;;
    logs) printf '%s\n' "$VICTORIA_LOGS_ADDR" ;;
    *) fatal "unknown component '$1'" ;;
  esac
}

extra_args_for() {
  case "$1" in
    metrics) printf '%s\n' "${VICTORIA_METRICS_ARGS:-}" ;;
    traces) printf '%s\n' "${VICTORIA_TRACES_ARGS:-}" ;;
    logs) printf '%s\n' "${VICTORIA_LOGS_ARGS:-}" ;;
    *) fatal "unknown component '$1'" ;;
  esac
}

pid_file_for() {
  printf '%s/%s.pid\n' "$VICTORIA_PID_DIR" "$1"
}

log_file_for() {
  printf '%s/%s.log\n' "$VICTORIA_LOG_DIR" "$1"
}

data_dir_for() {
  printf '%s/%s\n' "$VICTORIA_DATA_DIR" "$1"
}

binary_path_for() {
  printf '%s/%s\n' "$VICTORIA_BIN_DIR" "$(binary_name_for "$1")"
}

is_running_pid() {
  local pid="$1"
  is_positive_pid "$pid" && kill -0 "$pid" >/dev/null 2>&1
}

is_positive_pid() {
  local pid="$1"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
    *) [ "$pid" -gt 0 ] 2>/dev/null ;;
  esac
}

pid_matches_component() {
  local pid="$1" component="$2" binary command_line
  is_positive_pid "$pid" || return 1
  binary="$(binary_name_for "$component")"
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command_line" in
    *"$binary"*) return 0 ;;
    *) return 1 ;;
  esac
}

component_is_running() {
  local component="$1" pid_file pid
  pid_file="$(pid_file_for "$component")"
  [ -f "$pid_file" ] || return 1
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  is_running_pid "$pid" && pid_matches_component "$pid" "$component"
}

download_component() {
  local component="$1" url archive path tmp
  require_macos_arm64
  init_dirs
  url="$(url_for "$component")"
  archive="$(archive_name_for "$component")"
  path="$VICTORIA_DOWNLOAD_DIR/$archive"
  tmp="$path.tmp"
  if [ -s "$path" ]; then
    log "$component archive already exists: $path"
    return 0
  fi
  log "downloading $component: $url"
  curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$url"
  mv "$tmp" "$path"
}

extract_component() {
  local component="$1" archive path binary dest tmp
  require_macos_arm64
  init_dirs
  archive="$(archive_name_for "$component")"
  path="$VICTORIA_DOWNLOAD_DIR/$archive"
  binary="$(binary_name_for "$component")"
  dest="$VICTORIA_BIN_DIR/$binary"
  [ -s "$path" ] || download_component "$component"
  tmp="$(mktemp -d "$VICTORIA_HOME/extract-${component}.XXXXXX")"
  tar -xzf "$path" -C "$tmp"
  if [ ! -f "$tmp/$binary" ]; then
    rm -rf "$tmp"
    fatal "archive $path did not contain expected binary $binary"
  fi
  cp "$tmp/$binary" "$dest"
  chmod +x "$dest"
  rm -rf "$tmp"
  log "installed $component binary: $dest"
}

verify_component() {
  local component="$1" archive path binary url
  require_macos_arm64
  init_dirs
  archive="$(archive_name_for "$component")"
  path="$VICTORIA_DOWNLOAD_DIR/$archive"
  binary="$(binary_name_for "$component")"
  if [ -s "$path" ]; then
    if tar -tzf "$path" | grep -qx "$binary"; then
      log "verified $component archive contains $binary: $path"
      return 0
    fi
    fatal "$component archive does not contain expected binary $binary: $path"
  fi
  url="$(url_for "$component")"
  log "verifying $component release URL: $url"
  curl -fsSIL --retry 3 --retry-delay 2 -o /dev/null "$url"
}

start_component() {
  local component="$1" bin addr data_dir log_file pid_file extra_args pid
  require_macos_arm64
  init_dirs
  bin="$(binary_path_for "$component")"
  [ -x "$bin" ] || extract_component "$component"
  if component_is_running "$component"; then
    log "$component already running (pid $(cat "$(pid_file_for "$component")"))"
    return 0
  fi
  addr="$(addr_for "$component")"
  data_dir="$(data_dir_for "$component")"
  log_file="$(log_file_for "$component")"
  pid_file="$(pid_file_for "$component")"
  extra_args="$(extra_args_for "$component")"
  mkdir -p "$data_dir"
  log "starting $component on http://$addr (log: $log_file)"
  # Intentional word-splitting for optional operator-provided extra args.
  # shellcheck disable=SC2086
  nohup "$bin" -httpListenAddr="$addr" -storageDataPath="$data_dir" $extra_args >>"$log_file" 2>&1 &
  pid="$!"
  echo "$pid" >"$pid_file"
  sleep 0.2
  if ! is_running_pid "$pid"; then
    tail -50 "$log_file" >&2 || true
    fatal "$component failed to start"
  fi
}

stop_component() {
  local component="$1" pid_file pid i
  pid_file="$(pid_file_for "$component")"
  if [ ! -f "$pid_file" ]; then
    log "$component not running (no pid file)"
    return 0
  fi
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if ! is_positive_pid "$pid"; then
    rm -f "$pid_file"
    log "$component not running (invalid pid file removed)"
    return 0
  fi
  if ! is_running_pid "$pid"; then
    rm -f "$pid_file"
    log "$component not running (stale pid file removed)"
    return 0
  fi
  if ! pid_matches_component "$pid" "$component"; then
    fatal "$component pid file points to a non-Victoria process: $pid"
  fi
  log "stopping $component (pid $pid)"
  kill "$pid" >/dev/null 2>&1 || true
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! is_running_pid "$pid"; then
      rm -f "$pid_file"
      return 0
    fi
    sleep 1
  done
  log "$component still running after SIGTERM; sending SIGKILL"
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
}

status_component() {
  local component="$1" pid_file pid
  pid_file="$(pid_file_for "$component")"
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if component_is_running "$component"; then
    printf '%-8s running pid=%s url=http://%s\n' "$component" "$pid" "$(addr_for "$component")"
  else
    printf '%-8s stopped url=http://%s\n' "$component" "$(addr_for "$component")"
  fi
}

wait_component() {
  local component="$1" addr url i
  addr="$(addr_for "$component")"
  url="http://$addr/health"
  for i in $(seq 1 "${VICTORIA_WAIT_ATTEMPTS:-60}"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$component ready: $url"
      return 0
    fi
    sleep "${VICTORIA_WAIT_INTERVAL:-1}"
  done
  fatal "$component did not become ready at $url"
}

print_endpoints() {
  cat <<ENDPOINTS
VICTORIA_METRICS_ENDPOINT=http://$VICTORIA_METRICS_ADDR/opentelemetry/v1/metrics
VICTORIA_TRACES_ENDPOINT=http://$VICTORIA_TRACES_ADDR/insert/opentelemetry/v1/traces
VICTORIA_LOGS_ENDPOINT=http://$VICTORIA_LOGS_ADDR/insert/opentelemetry/v1/logs
VICTORIA_METRICS_UI=http://$VICTORIA_METRICS_ADDR/vmui/
VICTORIA_TRACES_UI=http://$VICTORIA_TRACES_ADDR/select/vmui/
VICTORIA_LOGS_UI=http://$VICTORIA_LOGS_ADDR/select/vmui/
ENDPOINTS
}

run_for_components() {
  local action="$1" requested="${2:-all}" component
  for component in $(components_for "$requested"); do
    "$action" "$component"
  done
}

main() {
  local command="${1:-help}" component="${2:-all}"
  case "$command" in
    init)
      require_macos_arm64
      init_dirs
      log "initialized $VICTORIA_HOME"
      ;;
    download) run_for_components download_component "$component" ;;
    verify) run_for_components verify_component "$component" ;;
    extract) run_for_components extract_component "$component" ;;
    install)
      require_macos_arm64
      init_dirs
      run_for_components download_component "$component"
      run_for_components extract_component "$component"
      ;;
    start) run_for_components start_component "$component" ;;
    stop) run_for_components stop_component "$component" ;;
    restart)
      run_for_components stop_component "$component"
      run_for_components start_component "$component"
      ;;
    status) run_for_components status_component "$component" ;;
    wait) run_for_components wait_component "$component" ;;
    endpoints) print_endpoints ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
