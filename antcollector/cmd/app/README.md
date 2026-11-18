## 说明
- 既可以 daemonset 部署也可以 deployment 部署，镜像构造方式有区别。
- 启动时需要通过 `--config` 指定配置文件路径。

<a id="容器健康探针"></a>

## Container health probes

```bash
/app/collector healthcheck
/app/collector healthcheck --ready
```

Without arguments, the command checks local liveness by requesting
`http://127.0.0.1:13133/debug/pprof/`. HTTP 200 within two seconds produces exit
code 0. It does not read downstream configuration or contact dependencies.

With `--ready`, the command checks local liveness, sends an empty POST without
credentials to `http://127.0.0.1:4318/v1/traces` and requires HTTP 401, then
requires HTTP 200 from `${SIGNOZ_INGEST_ENDPOINT}/api/v1/ready`. The downstream
environment variable is required in this mode. Each of the three sequential
requests has a two-second timeout; give the deployment probe at least eight
seconds. A missing endpoint, connection error, timeout, redirect, unexpected
status or unsupported argument produces a nonzero exit code. Dependency failure
affects readiness only, so Kubernetes liveness must use the local check.

The data-backend readiness endpoint executes a read-only telemetry-store
`SELECT 1` with a one-second server deadline. It returns HTTP 503 on database
failure while local `/api/v1/health` remains available. A data-backend image
without `/api/v1/ready` fails readiness; the gateway does not fall back to the
HTTP-only health endpoint.

The readiness requests contain no telemetry or credentials. Passing them does
not prove that authenticated telemetry can be stored and queried; validate
that separately with a signed OTLP request and API readback.

Both modes use the Collector binary in the scratch image. Compose can execute
`["CMD", "/app/collector", "healthcheck", "--ready"]` directly, without a shell,
curl or Bash. The image retains its system CA bundle, embeds timezone data for
`TZ`, and supports a read-only root filesystem with the default pipeline.
Custom configurations that write logs or profiles need writable mounts.

Run the probe unit tests with `go test -mod=vendor ./cmd/app`.
