#!/usr/bin/env bash
# Generate TypeScript types from proto files using buf.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_DIR="$REPO_ROOT/proto"

cd "$PROTO_DIR"
npx --yes @bufbuild/buf generate

echo "TypeScript proto types generated in vfe/src/proto/generated/"
