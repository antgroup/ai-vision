# Build from the repository root with docker build --network=host -f this file.
ARG CODEX_BASE_IMAGE=node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
ARG RUST_BASE_IMAGE=rust:slim@sha256:70d3b1a5e21806b8615c3fb2a59abea6e931aa29bf93f0a9d9c46e743beae096
FROM ${CODEX_BASE_IMAGE} AS codex
ARG TARGETARCH
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
RUN --mount=type=cache,target=/root/.npm,sharing=locked set -eu; \
    case "$TARGETARCH" in arm64) codex_arch=arm64 ;; amd64) codex_arch=x64 ;; *) exit 1 ;; esac; \
    if test -f /opt/codex/node_modules/@openai/codex/package.json; then \
      node -e 'if(require("/opt/codex/node_modules/@openai/codex/package.json").version!=="0.161.0" || require("/opt/codex/node_modules/@openai/codex-linux-"+process.argv[1]+"/package.json").version!=="0.161.0-linux-"+process.argv[1]) process.exit(1)' "$codex_arch"; \
    else \
      npm_config_proxy="$HTTP_PROXY" npm_config_https_proxy="$HTTPS_PROXY" \
      npm install --fetch-retries=1 --fetch-timeout=600000 --prefix /opt/codex --no-audit --no-fund \
        @openai/codex@0.161.0 "@openai/codex-linux-$codex_arch@npm:@openai/codex@0.161.0-linux-$codex_arch"; \
    fi

FROM ${RUST_BASE_IMAGE} AS base
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
RUN if ! command -v git || ! command -v python3 || ! command -v python; then \
      http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" apt-get -o Acquire::http::Pipeline-Depth=0 update && \
      http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" apt-get -o Acquire::http::Pipeline-Depth=0 \
        install -y --no-install-recommends git ca-certificates python3 python-is-python3 && rm -rf /var/lib/apt/lists/*; \
    fi

FROM base AS hooks
ARG TARGETARCH
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
COPY ai-vision/agent-plugins/agentic-otel-common/Cargo.toml /build/agentic-otel-common/Cargo.toml
COPY ai-vision/agent-plugins/agentic-otel-common/src /build/agentic-otel-common/src
COPY ai-vision/agent-plugins/codex/cdxhooks/Cargo.toml ai-vision/agent-plugins/codex/cdxhooks/Cargo.lock ai-vision/agent-plugins/codex/cdxhooks/build.rs /build/codex/cdxhooks/
COPY ai-vision/agent-plugins/codex/cdxhooks/src /build/codex/cdxhooks/src
WORKDIR /build/codex/cdxhooks
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=aiv-cdxhooks-${TARGETARCH},target=/cargo-target,sharing=locked \
    set -eu; HOOK_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -n 1)" \
    cargo build --locked --release --no-default-features --features common,tool_context \
      --bin cdxhooks --target-dir /cargo-target; \
    cp /cargo-target/release/cdxhooks /build/cdxhooks-binary

FROM base AS runtime
ARG TARGETARCH
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /opt/codex /opt/codex
COPY --from=hooks /build/cdxhooks-binary /usr/local/bin/cdxhooks
# Use the pinned official binary's argv0 dispatcher, not a replacement patcher.
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) codex_arch=arm64; codex_target=aarch64-unknown-linux-musl ;; \
      amd64) codex_arch=x64; codex_target=x86_64-unknown-linux-musl ;; \
      *) exit 1 ;; \
    esac; \
    codex_binary="/opt/codex/node_modules/@openai/codex-linux-$codex_arch/vendor/$codex_target/bin/codex"; \
    test -x "$codex_binary"; \
    ln -sf "$codex_binary" /usr/local/bin/apply_patch; \
    python --version
ENTRYPOINT ["/usr/bin/env"]
