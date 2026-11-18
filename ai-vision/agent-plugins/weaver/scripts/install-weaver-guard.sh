#!/bin/sh
set -eu

OSS_PREFIX="${WEAVER_GUARD_OSS_PREFIX:-${AIVISION_GUARD_OSS_PREFIX:-}}"
: "${OSS_PREFIX:?Set WEAVER_GUARD_OSS_PREFIX to your asset host}"
AIVISION_HOME="${AIVISION_HOME:-$HOME/.aivision}"
INSTALL_DIR="$AIVISION_HOME/bin"
INSTALL_PATH="$INSTALL_DIR/weaver-guard"

case "$(uname -s):$(uname -m)" in
  Darwin:arm64) ;;
  *)
    echo "weaver-guard installer supports macOS arm64 only." >&2
    exit 1
    ;;
esac

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to install weaver-guard." >&2
  exit 1
fi

prefix="${OSS_PREFIX%/}/"
version="$(curl -fsSL "${prefix}latest.txt" | tr -d '\r\n')"
if [ -z "$version" ]; then
  echo "latest.txt did not contain a version." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
tmp_file="$(mktemp "$INSTALL_DIR/.weaver-guard.XXXXXX")"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT INT TERM

primary_url="${prefix}${version}/weaver-guard"

echo "Installing weaver-guard ${version} to ${INSTALL_PATH}"
curl -fL "$primary_url" -o "$tmp_file"

chmod 0755 "$tmp_file"
mv -f "$tmp_file" "$INSTALL_PATH"

echo "Installed: ${INSTALL_PATH}"
echo "Add ${INSTALL_DIR} to PATH if needed."
