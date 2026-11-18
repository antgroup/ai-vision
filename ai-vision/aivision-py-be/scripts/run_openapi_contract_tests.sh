#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${BACKEND_DIR}"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required to run OpenAPI contract tests." >&2
  exit 127
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-aivision_backend.settings}"
export OTEL_ENABLED="${OTEL_ENABLED:-false}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export USE_SQLITE_FOR_TESTS="${USE_SQLITE_FOR_TESTS:-true}"

echo "[openapi-contract] Running deterministic Django contract tests."
echo "[openapi-contract] Uses Django test DB and an isolated local Langfuse HTTP test server."
echo "[openapi-contract] Unauthorized/Forbidden log lines are expected negative auth test cases."

uv run python - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required for OpenAPI contract tests")
PY

uv run python manage.py test \
  apps.core.tests.test_openapi_contract \
  apps.core.tests.test_openapi_events \
  apps.core.tests.test_openapi_trace \
  apps.core.tests.test_unified_auth \
  apps.core.tests.test_space_key_personal \
  apps.core.tests.test_signoz_trace_provider \
  apps.core.tests.test_trace_provider_contract
