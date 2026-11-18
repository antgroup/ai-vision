#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Cleaning dist/"
npm run clean

echo "==> Building (tsc + copy manifest)"
npm run build

echo "==> Verifying packed plugin can load without extra installs"
npm run verify:packed

echo "==> Publishing to tnpm"
tnpm publish

echo "==> Done ✅"
