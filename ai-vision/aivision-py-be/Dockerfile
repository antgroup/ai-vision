# AI Vision backend (Django) - self-hosted distribution image.
#
# Build context is the parent `ai-vision/` directory so the Agent-market
# catalog and onboarding scripts from `agent-plugins/` can be baked in next to
# the backend:
#
#   docker build -f aivision-py-be/Dockerfile -t aivision-be .
#
# Pre-built plugin artifacts (the multi-platform hook binaries and plugin
# tarballs produced by .github/workflows/release-plugins.yml) are expected at
# `agent-plugins/dist/` in the build context; they are served to coding agents
# by the backend's onboarding endpoints. An empty dist/ yields an empty market.

# Keep the virtual environment's interpreter identical across build/runtime.
FROM python:3.11.14-slim-bookworm@sha256:549988ff0804593d8373682ef5c0f0ceee48328abaaa2e054241c23f5c324751 AS python-base

# ── Stage 1: resolve the Python environment ─────────────────────────────────
FROM python-base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.30@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /usr/local/bin/uv
ARG UV_HTTP_TIMEOUT=300

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# The backend is a virtual (non-packaged) project: the lock alone determines
# the venv, so dependency resolution is cached independently of source edits.
WORKDIR /app
COPY aivision-py-be/pyproject.toml aivision-py-be/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

# Strip debug metadata before copying dependencies into the runtime image.
# Keep native code, dynamic symbols and all database authentication plugins.
# binutils stays in this build stage; it is not a runtime dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils \
    && rm -rf /var/lib/apt/lists/* \
    && find /app/.venv -type f \( -name '*.so' -o -name '*.so.*' \) \
        -exec strip --strip-debug --preserve-dates {} +

COPY aivision-py-be/ ./
ARG AIVISION_BUILD_INFO_BASE64=e30=
RUN printf '%s' "$AIVISION_BUILD_INFO_BASE64" | base64 -d > aivision_backend/build-info.json \
    && chmod 444 aivision_backend/build-info.json

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python-base AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/aivision /agent-plugins/dist

WORKDIR /app
COPY --from=builder /app /app

# Agent market: catalog + the onboarding/support scripts the backend serves.
# Paths mirror plugin_artifacts.SUPPORT_FILES and views/onboarding.py.
COPY agent-plugins/agents.json /agent-plugins/agents.json
COPY agent-plugins/scripts/connect \
     agent-plugins/scripts/install.sh \
     agent-plugins/scripts/native_config.py \
     agent-plugins/scripts/hook_runtime.py \
     /agent-plugins/scripts/
COPY agent-plugins/opencode/scripts/register-opencode-plugin.mjs /agent-plugins/opencode/scripts/
COPY agent-plugins/pi/scripts/configure-local-install.mjs /agent-plugins/pi/scripts/
COPY agent-plugins/deepseek/scripts/install-profile.mjs /agent-plugins/deepseek/scripts/
# Pre-built, multi-platform plugin artifacts. The `dis[t]` glob makes the
# directory optional: a dev build from a fresh clone (no dist/) still succeeds,
# it just ships an empty market.
COPY agent-plugins/dis[t]/ /agent-plugins/dist/
# Framework onboarding demos. configure_third_party_agents reads each catalog
# entry's `examples_dir` (relative to plugins_root) to build the "接入方式"
# framework list served to the onboarding pages; a declared examples_dir that
# is absent from the image makes that list collapse to just "Custom", so every
# `examples_dir` referenced by agents.json must be baked in here.
COPY agent-plugins/langfuse/examples /agent-plugins/langfuse/examples
COPY agent-plugins/nvidia-nemo-relay/examples /agent-plugins/nvidia-nemo-relay/examples
COPY agent-plugins/nvidia-nemo-relay/relay_tracing.py /agent-plugins/nvidia-nemo-relay/relay_tracing.py

COPY aivision-py-be/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AIVISION_ENV=container \
    AIVISION_AGENT_PLUGINS_DIR=/agent-plugins \
    AIVISION_PLUGIN_ARTIFACTS_DIR=/agent-plugins/dist \
    AIVISION_LOG_DIR=/var/log/aivision \
    BE_PORT=8001

EXPOSE 8001
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
