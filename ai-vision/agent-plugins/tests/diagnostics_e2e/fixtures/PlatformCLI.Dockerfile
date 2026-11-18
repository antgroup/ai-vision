# Build this locally before docker_platform.py; no deployment CLI runs on macOS.
FROM docker:cli AS docker
FROM golang:1.25.7-bookworm
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
ENTRYPOINT ["/usr/bin/env"]
