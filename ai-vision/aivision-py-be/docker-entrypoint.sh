#!/bin/bash
# Boot sequence for the aivision-be image.
#
# Schema migrations and the baseline platform catalog are folded into every
# boot: migrate / ensure_platform_config / seed_data are all idempotent, and
# running them here means a fresh datastore (or a redeploy after a wipe) needs
# no external orchestration step. The SigNoz integration bootstrap and the
# Agent-market configuration are NOT run here: they depend on signoz-ob being
# reachable and are driven by the deployment tool (aiv cast postStart hooks) or
# can be invoked explicitly with `docker exec <be> manage.py <command>`.
#
# Any arguments override the default server command, e.g.
#   docker run ... aivision-be python manage.py shell
set -euo pipefail

cd /app

# Native distributions provide a CPython launcher; checkouts retain manage.py.
MANAGE=(python manage.py)
if [ -x ./manage ]; then
  MANAGE=(./manage)
fi

"${MANAGE[@]}" migrate --noinput
"${MANAGE[@]}" ensure_platform_config
"${MANAGE[@]}" seed_data

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec "${MANAGE[@]}" serve_with_projector --address "0.0.0.0:${BE_PORT:-8001}"
