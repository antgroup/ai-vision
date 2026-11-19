# AI Vision OTLP 网关

Antcollector 接收经授权的 OTLP traces、logs、metrics，通过 OTLP HTTP 转发到 SigNoz ingest 端点。SigNoz 负责存储写入与查询。

## 组件

| 阶段 | 组件 | 用途 |
|---|---|---|
| 接收 | `otlp` | HTTP / gRPC 三类遥测 |
| 认证 | `aivisionauth` | 校验平台签发的采集令牌 |
| 处理 | `memory_limiter`、`spaceauth`、`modelbilling`、`batch` | 限制内存、写入可信空间与 Agent 身份、识别模型订阅、批处理 |
| 转发 | `otlphttp/signoz` | 携带内部密钥，将三类遥测转发到 SigNoz |
| 调试 | `pprof` | 性能诊断 |

唯一部署配置为 [gateway.yaml](conf/antcollectorsignoz/gateway.yaml)。空间身份来自认证结果，客户端自报属性不能覆盖它。
Agent 插件直接写入外部 Langfuse 时继续使用原 exporter。AI Vision 的 Langfuse 兼容入口将通过认证的 OTLP 转发至此网关。

The gateway accepts OTLP HTTP and gRPC requests up to 64 MiB. After trace
processors combine incoming spans, the OTLP HTTP exporter splits outgoing
trace requests at 60 MiB using their configured protobuf or JSON encoding.
Each piece enters the existing exporter queue independently. An individual
span that exceeds this bound is reported as a permanent rejection while
other spans continue. Recoverable failures retain only their failed spans
for retry. Media capture integrations apply their file and aggregate budgets
before export so oversized media can retain text and explicit budget markers.

## Model billing evidence

The trace pipeline preserves a per-call `aivision.billing.mode` supplied by
the model connection (`subscription` or `pay_as_you_go`). If the mode is absent,
`modelbilling` recognizes dedicated model endpoints for Baidu Qianfan Token Plan
Personal and GLM Coding Plan OpenAI Chat Completions (BigModel and Z.ai).
It uses `url.full`, then `http.url`, or the combination of `server.address`
and `url.path` from the same model span. A hostname alone is insufficient.

The processor fills `gen_ai.provider.name` when missing and can correct a
model-prefix guess marked by the Langfuse compatibility bridge. An explicit
provider is preserved. For shared routes, custom proxies, and other providers,
report the mode explicitly for each model call. Subscription identification
does not use or require a provider credential.

## 构建与验证

源码构建需要 Go 1.26.11 或更高版本。

```bash
go build -mod=vendor -o app ./cmd/app
go test -mod=vendor ./...
./app --config conf/antcollectorsignoz/gateway.yaml
```

运行前设置以下环境变量：

| 变量 | 用途 |
|---|---|
| `AIVISION_INGEST_SECRET` | 与 AI Vision 后端一致的采集令牌签名密钥 |
| `SIGNOZ_INGEST_ENDPOINT` | SigNoz ingest 地址，例如 `http://signoz-ob:8080` |
| `SIGNOZ_INGEST_INTERNAL_KEY` | 与 SigNoz 一致的内部转发密钥 |

整套服务部署见 [Docker 部署](https://aivision.mintlify.site/zh-CN/deployment/docker)。

## scratch 运行镜像

镜像保留静态 Collector、默认配置和原 Alpine 信任库的 CA 证书，时区数据嵌入 Go 二进制，可通过 `TZ` 配置。默认管线支持只读根文件系统，不需要额外的可写 `/tmp`；自定义文件日志或性能剖析输出需要挂载对应的可写目录。

Run `/app/collector healthcheck` for local liveness: the pprof endpoint on
`127.0.0.1:13133` must return HTTP 200 within two seconds. Add `--ready` to also
require an HTTP 401 from an empty, unauthenticated OTLP POST on port 4318 and
HTTP 200 from `${SIGNOZ_INGEST_ENDPOINT}/api/v1/ready`. Each request has a
two-second timeout; configure the outer readiness timeout to at least eight
seconds. Redirects, unexpected HTTP statuses and unsupported arguments fail.
The data backend must implement this SQL readiness endpoint: it runs a read-only
`SELECT 1` with a one-second deadline and returns HTTP 503 on database failure.
Its plain `/api/v1/health` stays available during a database outage and is not a
readiness substitute. Older images returning HTTP 404 fail the check.
Downstream failures affect readiness only. These probes do not replace signed
OTLP ingestion and query validation.

Compose 部署需要同时使用包含原生探针配置的新版 CLI/blueprint。从源码构建时应一起更新 CLI，避免旧健康检查继续调用镜像中不存在的 curl 或 Bash。产品日常状态与日志使用 `aiv status`、`aiv logs -n 80 ingest-gateway`，并保留原 target、namespace、workdir、blueprint 或 Compose file/env 参数。镜像没有 shell；底层只读排障可用宿主机的 `docker logs` 查看日志、`docker cp` 检查配置，或访问已映射的 pprof 端口。探针的直接执行方式见 [命令说明](cmd/app/README.md#container-health-probes)。

依赖采用 vendor 模式。网关基于公开上游版本构建，并在 vendor 中保留下述兼容补丁。裁剪基于网关和组件测试在 Linux amd64/arm64、macOS arm64 的依赖集合。保留模块的平台源码、嵌入资源与许可证。

The vendored `go-m1cpu` frequency reader checks for missing, non-data, or truncated
IOKit voltage-state properties before reading them. Some Apple Silicon hosts do
not expose these properties; CPU frequency stays unavailable (zero) instead of
crashing the gateway during package initialization. Preserve this guard when
refreshing vendor until the upstream dependency provides equivalent validation.

The vendored OTLP HTTP receiver carries a local retry-status patch in
`vendor/go.opentelemetry.io/collector/receiver/otlpreceiver/otlphttp.go`.
Export failures already classified as gRPC `Unavailable` (including memory
limiter refusals) return HTTP 503 with `Retry-After: 1`; permanent and unknown
export failures retain HTTP 500. The receiver also registers each configured
route alongside the standard route, with the same authentication middleware.
Preserve these patches when refreshing vendor. The public-factory
regression test at `scripts/test/receiver/otlphttp_test.go` runs in the normal
`go test -mod=vendor ./...` suite and checks all three signals, default and
custom routes, JSON and protobuf, recovery, malformed input, and authentication.

The vendored HTTP configuration applies the configured body size to both
wire bytes and decoded bytes, and streams Snappy requests. The gateway carries the upstream body-limit behavior.
[OpenTelemetry Collector change #10289](https://github.com/open-telemetry/opentelemetry-collector/pull/10289)

The gateway uses OpenTelemetry Collector's built-in receiver and exporter
self-telemetry. Its vendored exporter modules come from upstream `v0.96.0`,
with the trace request limit applied in the `exporter/boundedotlphttpexporter`
wrapper. The receiver starts from public upstream `v0.96.0`, and pdata starts
from public upstream `v1.3.0`. The local pdata compatibility code preserves
Table rows when decoding, copying, counting and forwarding OTLP. Use
`-mod=vendor` for product builds. Refreshing vendor requires retaining the
compatibility code and rerunning the receiver and pdata regression tests.
