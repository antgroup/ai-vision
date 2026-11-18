#!/usr/bin/env bash
# Upload cdxhooks release artifacts to OSS.
# Usage: HOOK_VERSION=0.1.0 OSS_BUCKET=your-bucket ./upload.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
VERSION="${HOOK_VERSION:-dev}"
OSS_BUCKET="${OSS_BUCKET:-}"
OSS_PREFIX="${OSS_PREFIX:-cdxhooks}"

if [[ -z "$OSS_BUCKET" ]]; then
    echo "ERROR: OSS_BUCKET environment variable is required"
    exit 1
fi

if [[ ! -d "$DIST_DIR" ]]; then
    echo "ERROR: dist/ directory not found. Run build_release.sh first."
    exit 1
fi

echo "Uploading cdxhooks $VERSION artifacts to oss://$OSS_BUCKET/$OSS_PREFIX/"

found=0
while IFS= read -r artifact; do
    found=1
    filename="$(basename "$artifact")"
    oss_path="oss://$OSS_BUCKET/$OSS_PREFIX/$VERSION/$filename"
    echo "Uploading $filename → $oss_path"
    ossutil cp -f "$artifact" "$oss_path"
done < <(find "$DIST_DIR" -type f -name "*-${VERSION}-*" | sort)

if [[ "$found" -eq 0 ]]; then
    echo "ERROR: no artifacts found under dist/ for version $VERSION"
    exit 1
fi

echo ""
echo "=== Upload complete ==="
