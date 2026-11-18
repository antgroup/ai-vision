# antcollector gateway - self-hosted distribution image.
#
#   docker build -t antcollector-gw .
#   docker buildx build --platform linux/amd64,linux/arm64 .
#
# The OTLP ingest gateway: verifies AI Vision ingest tokens (aivisionauth),
# stamps org/space onto telemetry (spaceauth) and forwards to signoz-ob. The
# gateway config is baked in; the deployment supplies the three environment
# variables it references:
#   AIVISION_INGEST_SECRET      shared with aivision-be (token signing/verify)
#   SIGNOZ_INGEST_ENDPOINT      http://<signoz-ob>:8080
#   SIGNOZ_INGEST_INTERNAL_KEY  shared with signoz-ob
# Mount a different file at /etc/otelcol/config.yaml to override the pipeline.

# ── Stage 1: build (vendored modules => fully offline, cross-compiled) ──────
FROM --platform=$BUILDPLATFORM golang:1.25-bookworm AS builder

ARG TARGETOS
ARG TARGETARCH
# The collector has no cgo dependencies, so a static cross-compiled binary is
# produced on the build platform without QEMU.
ENV CGO_ENABLED=0 \
    GOFLAGS=-mod=vendor \
    GOTOOLCHAIN=local

WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/go-build \
    GOOS=$TARGETOS GOARCH=$TARGETARCH go build -ldflags "-s -w" -o /out/collector ./cmd/app

# Keep the existing Alpine trust store; the Go builder uses a different CA set.
# Only the architecture-independent PEM bundle enters the runtime image.
# Use the supported branch's current package; Alpine retires older revisions
# from its package index, which makes exact apk revision pins fail on fresh builds.
FROM --platform=$BUILDPLATFORM alpine:3.22 AS runtime-assets
RUN apk add --no-cache ca-certificates
ARG AIVISION_BUILD_INFO_BASE64=e30=
RUN mkdir -p /etc/aivision \
    && printf '%s' "$AIVISION_BUILD_INFO_BASE64" | base64 -d > /etc/aivision/build-info.json \
    && chmod 444 /etc/aivision/build-info.json

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM scratch

# Go embeds timezone data; retain the system trust store for TLS exporters.
COPY --from=runtime-assets /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /out/collector /app/collector
COPY conf/antcollectorsignoz/gateway.yaml /etc/otelcol/config.yaml
COPY --from=runtime-assets /etc/aivision/build-info.json /etc/aivision/build-info.json

# 4317 OTLP/gRPC, 4318 OTLP/HTTP, 13133 pprof (collector healthcheck probe).
EXPOSE 4317 4318 13133
ENTRYPOINT ["/app/collector"]
CMD ["--config", "/etc/otelcol/config.yaml"]
