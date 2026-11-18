#!/bin/bash
# Boot sequence for the agentlens self-hosted image.
#
# The uvicorn startup worker reconciles agentlens_task immediately, so the
# control-plane schema must exist BEFORE the server starts. Migrations are
# idempotent (alembic upgrade head) and retried briefly to absorb a datastore
# that accepts TCP a few seconds before its SQL layer is ready.
#
# Any arguments override the default server command.
set -euo pipefail
native_migrate=/app/agentlens-migrate

# Compose bootstrap publishes only the non-sensitive organization identity.
# Explicit environment configuration remains authoritative for existing users.
if [ -z "${SIGNOZ_ORG_ID:-}" ] && [ -n "${SIGNOZ_ORG_ID_FILE:-}" ]; then
  if [ -x "$native_migrate" ]; then
    SIGNOZ_ORG_ID="$("$native_migrate" read-org-id-file)"
  else
    SIGNOZ_ORG_ID="$(python -m agentlens.bootstrap)"
  fi
  export SIGNOZ_ORG_ID
fi

cd /app

attempts="${AGENTLENS_MIGRATE_ATTEMPTS:-12}"
migrate() {
  if [ -x "$native_migrate" ]; then
    "$native_migrate" -c obschema/alembic.ini upgrade head
  else
    alembic -c obschema/alembic.ini upgrade head
  fi
}
for i in $(seq 1 "$attempts"); do
  if migrate; then
    break
  fi
  if [ "$i" -eq "$attempts" ]; then
    echo "agentlens: migrations still failing after $attempts attempts" >&2
    exit 1
  fi
  echo "agentlens: migration attempt $i/$attempts failed (datastore not ready?), retrying in 5s" >&2
  sleep 5
done

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec python -m uvicorn --factory agentlens.main:build_app --host 0.0.0.0 --port "${AGENTLENS_PORT:-8081}"
