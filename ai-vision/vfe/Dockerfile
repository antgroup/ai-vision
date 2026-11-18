# AI Vision frontend (React / Vite) - self-hosted distribution image.
#
# Build context is this directory:
#
#   docker build -t aivision-fe .
#
# The production bundle is served by nginx, which also reverse-proxies /api and
# /openapi to the backend so the browser talks to a single origin (the same
# shape the vite dev-server proxy provides during development). The backend
# upstream is resolved at container start from $BE_UPSTREAM.

# ── Stage 1: build the static bundle ────────────────────────────────────────
# Only static assets enter the target image; build tools run on the host platform.
FROM --platform=$BUILDPLATFORM node:22-bookworm AS builder

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable pnpm

# vite.config.ts writes the bundle to ../dist, so build one level below /src.
WORKDIR /src/vfe
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
ARG AIVISION_BUILD_INFO_BASE64=e30=
RUN pnpm build

# ── Stage 2: nginx ──────────────────────────────────────────────────────────
FROM nginx:1.27-alpine-slim

# curl is required by deployment health checks.
RUN apk add --no-cache curl ca-certificates

COPY --from=builder /src/dist /usr/share/nginx/html
# The official image runs envsubst on /etc/nginx/templates/*.template at start.
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template

ENV BE_UPSTREAM=http://aivision-be:8001 \
    FE_PORT=5174 \
    AIVISION_TRUST_PROXY_HEADERS=false

EXPOSE 5174
