#!/usr/bin/env bash
# Compatibility for the previous install.sh URL. Installs no standalone CLI.
set -euo pipefail
DEFAULT_BASE_URL="__AIVISION_BASE_URL__"
AIVISION_BASE_URL="${AIVISION_BASE_URL:-$DEFAULT_BASE_URL}"
agent=""
args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url) AIVISION_BASE_URL="$2"; shift 2 ;;
    --installation-token|--space-key|--space-key-file|--space-id|--agent-uuid|--config-dir|--profiles) args+=("$1" "$2"); shift 2 ;;
    --dry-run) args+=("$1"); shift ;;
    plugin) shift; [ "${1:-}" = install ] || { echo 'Only plugin install is supported' >&2; exit 2; }; shift ;;
    cc|claude-code|cdx|codex|kimi|km|opencode|ocd|openclaw|ocw|pi|dsh|ds) agent="$1"; shift ;;
    *) echo 'Usage: install.sh [--base-url URL] plugin install <agent>' >&2; exit 2 ;;
  esac
done
[ -n "$agent" ] || { echo 'An Agent is required' >&2; exit 2; }
command -v python3 >/dev/null || { echo 'Python 3.11+ is required' >&2; exit 2; }
aivision_tmp="$(mktemp -d)"
trap 'rm -rf "$aivision_tmp"' EXIT
curl -fsSL "${AIVISION_BASE_URL%/}/api/public/onboarding/scripts/connect" -o "$aivision_tmp/connect.py"
python3 "$aivision_tmp/connect.py" --agent "$agent" --base-url "$AIVISION_BASE_URL" "${args[@]}"
