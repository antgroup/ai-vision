#!/usr/bin/env bash
# Generate Python gRPC stubs for Jaeger V3 QueryService.
# OTLP proto classes come from the installed opentelemetry-proto package.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_DIR="$REPO_ROOT/proto"
OUT_DIR="$REPO_ROOT/aivision-py-be/apps/core/trace_query/jaeger_v3/generated"

mkdir -p "$OUT_DIR"

cd "$REPO_ROOT/aivision-py-be"
uv run python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  --pyi_out="$OUT_DIR" \
  "$PROTO_DIR/jaeger/api_v3/query_service.proto"

# Patch imports in grpc stub: protoc generates absolute imports that fail
# when the generated package is nested inside the Django app.
# Fix: "from jaeger.api_v3 import X" → "from . import X"
GRPC_FILE="$OUT_DIR/jaeger/api_v3/query_service_pb2_grpc.py"
if [ -f "$GRPC_FILE" ]; then
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' 's/from jaeger\.api_v3 import /from . import /g' "$GRPC_FILE"
  else
    sed -i 's/from jaeger\.api_v3 import /from . import /g' "$GRPC_FILE"
  fi
fi

echo "Python proto stubs generated in $OUT_DIR"
