#!/usr/bin/env bash
# Compile own modules and bootstrap helpers; stage locked dependencies separately.
# Requires target Linux CPython 3.11.14, Cython 3.2.4, setuptools and C headers.
# Does not create images or install packages. Runtime output must be empty.
set -euo pipefail
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${NATIVE_OUTPUT:?Set NATIVE_OUTPUT to an empty runtime staging directory}"
: "${NATIVE_BUILD_ROOT:?Set NATIVE_BUILD_ROOT outside the runtime staging directory}"
: "${NATIVE_BUILD_PYTHON:=python}"
: "${BOOTSTRAP_SOURCE_ROOT:=$SOURCE_ROOT/../../deploy/bootstrap}"
: "${CPYTHON_CONFIG:=python3-config}"
: "${NATIVE_JOBS:=1}"
exec "$NATIVE_BUILD_PYTHON" "$SOURCE_ROOT/scripts/native_build.py" \
  --source "$SOURCE_ROOT" --bootstrap-source "$BOOTSTRAP_SOURCE_ROOT" \
  --output "$NATIVE_OUTPUT" --build-root "$NATIVE_BUILD_ROOT" \
  --jobs "$NATIVE_JOBS" --python-config "$CPYTHON_CONFIG"
