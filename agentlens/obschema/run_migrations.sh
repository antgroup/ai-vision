#!/usr/bin/env bash
set -euo pipefail
alembic -c "$(dirname "$0")/alembic.ini" upgrade head
