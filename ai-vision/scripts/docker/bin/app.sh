#!/usr/bin/env bash
set -e

cd /home/admin/aivision

PYTHON=/opt/venv/bin/python

$PYTHON manage.py collectstatic --noinput

WORKERS="${GUNICORN_WORKERS:-4}"
BIND="${GUNICORN_BIND:-0.0.0.0:8080}"
WORKER_CLASS="${GUNICORN_WORKER_CLASS:-gthread}"
THREADS="${GUNICORN_THREADS:-100}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"
GRACEFUL_TIMEOUT="${GUNICORN_GRACEFUL_TIMEOUT:-30}"
KEEP_ALIVE="${GUNICORN_KEEP_ALIVE:-5}"
MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-1000}"
MAX_REQUESTS_JITTER="${GUNICORN_MAX_REQUESTS_JITTER:-200}"

echo "Starting Gunicorn on ${BIND}: workers=${WORKERS}, worker_class=${WORKER_CLASS}, threads=${THREADS}"

exec /opt/venv/bin/gunicorn aivision_backend.wsgi:application \
    --bind "${BIND}" \
    --workers "${WORKERS}" \
    --worker-class "${WORKER_CLASS}" \
    --threads "${THREADS}" \
    --timeout "${TIMEOUT}" \
    --graceful-timeout "${GRACEFUL_TIMEOUT}" \
    --keep-alive "${KEEP_ALIVE}" \
    --max-requests "${MAX_REQUESTS}" \
    --max-requests-jitter "${MAX_REQUESTS_JITTER}" \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --preload
