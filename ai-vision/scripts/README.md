# AI Vision Scripts

This directory documents the component container scripts. For the complete
product, follow the [deployment guide](../../docs/deployment/README.md) and
[CLI reference](../../aivision-cli/README.md). The product deployment assets
live in root [`deploy/`](../../deploy/README.md).

## Component Docker Image

The [component Dockerfile](docker/Dockerfile) installs backend dependencies
into `/opt/venv` and copies `aivision-py-be/` to `/home/admin/aivision` in
the runtime image.

At runtime, the container entrypoint keeps the container alive. Platform
startup calls:

```bash
/home/admin/bin/start.sh
```

`start.sh` starts `supervisord` and then starts the `app` program defined in:

```bash
scripts/docker/supervisord.d/app.ini
```

The app process runs:

```bash
scripts/docker/bin/app.sh
```

`app.sh` runs `collectstatic` and starts Gunicorn.
Supervisor also imports the bundled models.dev catalog if the migrated
database has no snapshot. The import runs independently of Gunicorn and
requires no network connection. For later updates, use [Model Pricing settings
or the offline import command](../docs/observability/model-pricing.md#upgrade-and-verification-boundaries).

## Gunicorn Runtime Defaults

The Docker runtime uses threaded Gunicorn workers for I/O-heavy dashboard and
trace query traffic:

```text
GUNICORN_WORKERS=4
GUNICORN_WORKER_CLASS=gthread
GUNICORN_THREADS=100
GUNICORN_TIMEOUT=120
GUNICORN_GRACEFUL_TIMEOUT=30
GUNICORN_KEEP_ALIVE=5
GUNICORN_MAX_REQUESTS=1000
GUNICORN_MAX_REQUESTS_JITTER=200
```

All values can be overridden by environment variables.

The default capacity is:

```text
4 workers * 100 threads = 400 in-process concurrent requests per container
```

This is intended for synchronous Django requests that spend significant time
waiting on downstream systems such as SigNoz or Langfuse. If CPU,
file descriptors, database connections, or downstream latency become the
bottleneck, tune these values through environment variables instead of editing
the script directly.

## Local Development

Follow the platform [development commands](../AGENTS.md#common-commands) to
start Django and Vite separately.

## Operational Commands

Inside the runtime container, use the supervisor wrapper:

```bash
sc status
sc start app
sc stop app
sc restart app
```

The wrapper talks to Supervisor on `127.0.0.1:9001`.

## Script Ownership

- `scripts/docker/Dockerfile`: component image build.
- `scripts/docker/bin/start.sh`: starts Supervisor and the app program.
- `scripts/docker/bin/app.sh`: collects static files and starts Gunicorn.
- `scripts/docker/supervisord.conf`: Supervisor daemon configuration.
- `scripts/docker/supervisord.d/app.ini`: app process configuration.
- `scripts/docker/supervisord.d/model-pricing.ini`: first catalog import.
