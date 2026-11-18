#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
PKG_NAME="observ-openclaw"

# Read version from package.json
VERSION=$(node -p "require('$PROJECT_DIR/package.json').version")
TARBALL="${PKG_NAME}-${VERSION}.tar.gz"

echo "=== Building ${PKG_NAME} v${VERSION} ==="
cd "$PROJECT_DIR"
npm run build

echo "=== Packing ${TARBALL} ==="
cd "$PARENT_DIR"
rm -f "$TARBALL"
tar -czf "$TARBALL" \
  --exclude="${PKG_NAME}/src" \
  --exclude="${PKG_NAME}/semconv" \
  --exclude="${PKG_NAME}/node_modules" \
  --exclude="${PKG_NAME}/scripts" \
  --exclude='*.md' \
  --exclude='*.d.ts' \
  --exclude='*.d.ts.map' \
  --exclude='*.js.map' \
  --exclude='.gitignore' \
  --exclude='.npmignore' \
  --exclude='tsconfig.json' \
  --exclude='index.ts' \
  --exclude='publish.sh' \
  --exclude='solved_issues' \
  --exclude='package-lock.json' \
  --exclude='*.tsbuildinfo' \
  --exclude='.eslintrc*' \
  --exclude='.prettierrc*' \
  "$PKG_NAME/"

echo "=== Done ==="
echo "Package: ${PARENT_DIR}/${TARBALL}"
ls -lh "${PARENT_DIR}/${TARBALL}"
