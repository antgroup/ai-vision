#!/bin/bash
# Start AgentLens (uvicorn, port 8081) for local dev against the signoz-ob stack.
#
# Two env details matter and are easy to get wrong:
#   1. AIVISION_ENV=dev -- AgentLens reads env/env.dev with this value. Do NOT
#      export the same value to the Django backend: its ENV_FILE_MAP has no
#      "dev" entry, so SECRET_KEY silently falls back to the default and every
#      JWT becomes invalid.
#   2. OTEL_EXPORTER_OTLP_TRACES_HEADERS -- the antcollector gateway authenticates
#      OTLP writes with the aivisionauth extension, which reads a space-scoped
#      HMAC ingest token from the Authorization header. AgentLens' own client only
#      sends X-Access-Id/X-Access-Token, and because it passes an EMPTY headers
#      dict the OTLP exporter falls back to this standard env var. Without it the
#      diagnosis-record/task_stats span batches are rejected with 401 and silently
#      dropped, while the task still reports saved_records>0.
#
# The token is re-read from ~/.codex/.env on every start, so renewing the token
# (scripts/renew_ingest_token.sh) plus a restart picks up the new value.
set -euo pipefail

ROOT="$HOME/Downloads/workspace/观测评测/aivision-signoz-ob"
LOG_DIR="${AGENTLENS_LOG_DIR:-/tmp/agentlens-logs-20260913}"
PORT="${AGENTLENS_PORT:-8081}"
mkdir -p "$LOG_DIR"

INGEST_TOKEN="$(grep -E '^CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS=' "$HOME/.codex/.env" | head -1 | sed 's/^.*Bearer //')"
if [ -z "$INGEST_TOKEN" ]; then
  echo "no ingest token in ~/.codex/.env; diagnosis records would be rejected with 401" >&2
  exit 1
fi

cd "$ROOT/agentlens"
export AIVISION_ENV=dev
export AGENTLENS_OB_URL="mysql+pymysql://root@127.0.0.1:2881/agentlens?charset=utf8mb4"
export SIGNOZ_OB_URL="mysql+pymysql://root@127.0.0.1:2881/signoz?charset=utf8mb4"
# The org is NOT hardcoded: it is produced by the backend bootstrap and read back
# out of the be container (signoz_integration_status --print-org emits ONLY the
# non-sensitive org id). Override SIGNOZ_ORG_ID to skip the lookup.
BE_CONTAINER="${BE_CONTAINER:-aivision-be}"
if [ -z "${SIGNOZ_ORG_ID:-}" ]; then
  SIGNOZ_ORG_ID="$(docker exec -w /app "$BE_CONTAINER" .venv/bin/python manage.py signoz_integration_status --print-org 2>/dev/null | tail -1 || true)"
fi
if [ -z "${SIGNOZ_ORG_ID:-}" ]; then
  echo "could not resolve SIGNOZ_ORG_ID from $BE_CONTAINER (is the backend bootstrap done?); set SIGNOZ_ORG_ID explicitly" >&2
  exit 1
fi
export SIGNOZ_ORG_ID
export AGENTLENS_TOKEN=dev-token
export ANTCOLLECTOR_ENDPOINT="http://127.0.0.1:4318/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer ${INGEST_TOKEN}"
export AGENTLENS_LOG_DIR="$LOG_DIR"
export AGENTLENS_PORT="$PORT"

exec nohup .venv/bin/uvicorn --factory agentlens.main:build_app \
  --host 0.0.0.0 --port "$PORT" > "$LOG_DIR/uvicorn-${PORT}.log" 2>&1
