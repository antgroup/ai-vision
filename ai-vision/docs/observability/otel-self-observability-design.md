# AI Vision 平台自观测设计文档

> **版本**: v1.0
> **日期**: 2026-05-14
> **作者**: AI Vision Team

## 目录

- [1. 背景与目标](#1-背景与目标)
- [2. 架构设计](#2-架构设计)
- [3. 技术选型](#3-技术选型)
- [4. 模块设计](#4-模块设计)
- [5. 指标清单](#5-指标清单)
- [6. 配置参考](#6-配置参考)
- [7. 使用指南](#7-使用指南)
- [8. 运维与排障](#8-运维与排障)
- [9. 后续演进](#9-后续演进)

---

## 1. 背景与目标

AI Vision 作为 AI Agent 可观测平台，为上层业务提供 Trace、Metrics、Logs 查询能力，但平台自身缺乏可观测性——无法回答以下问题：

- API 请求延迟分布如何？哪些接口最慢？
- 外部依赖（Langfuse、SigNoz）的调用成功率和延迟？
- Trace 查询的耗时分布？不同 Provider 的性能差异？
- 认证失败率？是否存在异常访问模式？

### 设计目标

1. **零侵入开关**：通过环境变量 `OTEL_ENABLED` 控制，默认关闭，关闭时零性能开销
2. **标准协议**：采用 OpenTelemetry SDK + OTLP 协议，后端可灵活对接 Prometheus / Jaeger / Grafana 等
3. **自动 + 自定义**：自动采集 HTTP/DB 指标，同时在关键业务路径埋点自定义指标
4. **日志关联**：每条日志自动携带 trace_id，实现 Traces ↔ Logs 的关联查询

---

## 2. 架构设计

```
┌──────────────────────────────────────────────────────────────┐
│                   Django App (Gunicorn)                        │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              OTel Auto-Instrumentation                    │  │
│  │  ┌──────────────┐ ┌───────────────┐ ┌────────────────┐  │  │
│  │  │    Django     │ │   Requests    │ │    Logging     │  │  │
│  │  │ Instrumentor │ │ Instrumentor  │ │ Instrumentor   │  │  │
│  │  │              │ │               │ │                │  │  │
│  │  │ HTTP server  │ │ HTTP client   │ │ trace_id 注入  │  │  │
│  │  │ metrics +    │ │ metrics +     │ │ 到 logging     │  │  │
│  │  │ traces       │ │ traces        │ │ record         │  │  │
│  │  └──────────────┘ └───────────────┘ └────────────────┘  │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              Custom Business Metrics                      │  │
│  │                                                           │  │
│  │  TraceQueryService    BaseQueryAdapter    Authentication  │  │
│  │  ├ duration (hist)    ├ duration (hist)   └ events (cnt)  │  │
│  │  └ count (counter)    └ errors (counter)                  │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │           TracerProvider + MeterProvider                   │  │
│  │  ┌──────────────────┐  ┌───────────────────────────┐     │  │
│  │  │ BatchSpanProcessor│  │ PeriodicExportingMetric   │     │  │
│  │  │                  │  │ Reader (60s interval)     │     │  │
│  │  └────────┬─────────┘  └─────────────┬─────────────┘     │  │
│  └───────────┼──────────────────────────┼────────────────────┘  │
└──────────────┼──────────────────────────┼────────────────────────┘
               │       OTLP/HTTP :4318     │
               ▼                          ▼
       ┌──────────────────────────────────────┐
       │          OTLP Collector              │
       │          localhost:4318              │
       │          /v1/traces                  │
       │          /v1/metrics                 │
       └──────────────────────────────────────┘
```

### 数据流

1. **入站请求** → Django Instrumentor 自动创建 server span + 记录 `http.server.request.duration`
2. **出站调用** → Requests Instrumentor 自动创建 client span + 记录 `http.client.request.duration`
3. **业务逻辑** → 自定义指标在 TraceQueryService / BaseQueryAdapter / Authentication 中手动记录
4. **日志输出** → Logging Instrumentor 自动将 `otelTraceID` / `otelSpanID` 注入每条日志
5. **数据导出** → BatchSpanProcessor (traces) + PeriodicExportingMetricReader (metrics) 通过 OTLP/HTTP 推送到 AntCollector

---

## 3. 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| **OTel SDK** | `opentelemetry-sdk` | Python OTel 官方实现，社区活跃 |
| **Exporter** | `opentelemetry-exporter-otlp` (HTTP/protobuf) | 标准协议，HTTP 代理友好，AntCollector 原生支持 |
| **Django 插桩** | `opentelemetry-instrumentation-django` | 官方维护，自动产生 HTTP 语义化指标和 traces |
| **HTTP 客户端插桩** | `opentelemetry-instrumentation-requests` | 自动追踪所有 `requests` 库调用 |
| **日志关联** | `opentelemetry-instrumentation-logging` | 自动注入 trace context 到 logging |
| **初始化模式** | SDK programmatic (非 agent 注入) | 不改部署脚本，支持条件开关，Gunicorn `--preload` 兼容 |

### 为什么选 SDK 模式而非 `opentelemetry-instrument` 注入

| 维度 | SDK programmatic | `opentelemetry-instrument` agent |
|------|-----------------|----------------------------------|
| 初始化控制 | 代码中按条件启用 | 命令行包裹，全局生效 |
| 部署侵入 | 不改 `start.sh` | 需改启动命令 |
| 自定义指标 | 初始化逻辑集中管理 | 同样需要代码 |
| Gunicorn 兼容 | `AppConfig.ready()` + `--preload` | 需额外配置 `post_fork` |
| 调试 | 可断点、可日志 | 黑盒 |

---

## 4. 模块设计

### 4.1 observability.py — OTel 初始化

**路径**: `aivision-py-be/aivision_backend/observability.py`

```python
init_observability()  # 唯一入口
```

职责：
- 检查 `OTEL_ENABLED` 环境变量，非 truthy 值直接返回
- 创建 `Resource`（service.name / service.version / deployment.environment）
- 初始化 `TracerProvider` + `BatchSpanProcessor` + `OTLPSpanExporter`
- 初始化 `MeterProvider` + `PeriodicExportingMetricReader` + `OTLPMetricExporter`
- 调用三个 Instrumentor 的 `.instrument()`

安全机制：
- `_initialized` 标志防止重复初始化
- 异常被 catch + log，不影响应用启动

调用时机：
- `apps.core.apps.CoreConfig.ready()` 中调用
- Gunicorn `--preload` 模式下在 fork 前执行一次

### 4.2 metrics.py — 自定义业务指标

**路径**: `aivision-py-be/apps/core/metrics.py`

所有 instruments 在模块导入时通过 `metrics.get_meter("aivision.core")` 创建。当 OTel 未启用时，全局 MeterProvider 为 no-op 实现，所有 `.record()` / `.add()` 调用零开销。

```python
from apps.core.metrics import trace_query_duration, trace_query_count
```

### 4.3 埋点位置

| 位置 | 文件 | 指标 | Labels |
|------|------|------|--------|
| Trace 查询 | `trace_query/service.py` | `trace_query_duration` (histogram) | provider, method |
| Trace 查询 | `trace_query/service.py` | `trace_query_count` (counter) | provider, method, status |
| 数据适配器 | `adapters/base.py` | `adapter_duration` (histogram) | adapter_type, data_item_key |
| 数据适配器 | `adapters/base.py` | `adapter_errors` (counter) | adapter_type, data_item_key, error_type |
| 认证 | `authentication.py` | `auth_events` (counter) | method, result |

认证埋点的 `method` label 取值包括：`iam`、`jwt`、`agentpass`。

---

## 5. 指标清单

### 5.1 自动采集指标（OTel Instrumentation）

#### HTTP Server（Django Instrumentor）

| 指标名 | 类型 | 单位 | Labels | 说明 |
|--------|------|------|--------|------|
| `http.server.request.duration` | Histogram | s | `http.request.method`, `http.route`, `http.response.status_code`, `url.scheme` | 每个 Django 请求的处理延迟 |
| `http.server.active_requests` | UpDownCounter | {request} | `http.request.method`, `url.scheme` | 当前活跃请求数 |
| `http.server.request.body.size` | Histogram | By | 同 request.duration | 请求体大小 |
| `http.server.response.body.size` | Histogram | By | 同 request.duration | 响应体大小 |

#### HTTP Client（Requests Instrumentor）

| 指标名 | 类型 | 单位 | Labels | 说明 |
|--------|------|------|--------|------|
| `http.client.request.duration` | Histogram | s | `http.request.method`, `server.address`, `server.port`, `http.response.status_code` | 外部 HTTP 调用延迟 |

覆盖的外部依赖：
- Langfuse Public API (`langfuse.cloud` / 内部部署)
- SigNoz 结构化查询 API

### 5.2 自定义业务指标

| 指标名 | 类型 | 单位 | Labels | 说明 |
|--------|------|------|--------|------|
| `aivision.trace_query.duration` | Histogram | s | `provider` (langfuse/signoz), `method` (list_traces/get_trace_detail/...) | Trace 查询端到端耗时 |
| `aivision.trace_query.count` | Counter | - | `provider`, `method`, `status` (success/error) | Trace 查询调用次数 |
| `aivision.adapter.duration` | Histogram | s | `adapter_type` (MetricsQueryAdapter/LogsQueryAdapter/...), `data_item_key` | 数据适配器 execute + parse 耗时 |
| `aivision.adapter.errors` | Counter | - | `adapter_type`, `data_item_key`, `error_type` | 数据适配器错误次数 |
| `aivision.auth.events` | Counter | - | `method` (iam/jwt/agentpass), `result` (success/failure) | 认证事件统计 |
| `aivision.v2_query.duration` | Histogram | s | `agent_product`, `query_key`, `scope` | V2 Dashboard 单次查询耗时 |
| `aivision.v2_query.count` | Counter | - | `agent_product`, `query_key`, `scope`, `status` | V2 Dashboard 查询调用次数 |
| `aivision.v2_query.errors` | Counter | - | `agent_product`, `query_key`, `error_type` | V2 Dashboard 查询错误次数 |
| `aivision.v2_batch_query.duration` | Histogram | s | `agent_product`, `scope` | V2 Dashboard 批量查询总耗时 |
| `aivision.external_call.duration` | Histogram | s | `service`, `method` | 外部服务调用耗时 |
| `aivision.external_call.errors` | Counter | - | `service`, `method`, `error_type` | 外部服务调用失败次数 |

### 5.3 Traces

Django Instrumentor 自动为每个入站 HTTP 请求创建 server span，包含：
- `http.method`, `http.route`, `http.status_code`, `http.url`
- 请求处理全程的 duration

Requests Instrumentor 自动为每个出站 HTTP 调用创建 client span，自动挂在 server span 下形成调用链。

### 5.4 Log Correlation

每条 Python `logging` 日志自动携带：
- `otelTraceID`: 32 字符 hex trace ID
- `otelSpanID`: 16 字符 hex span ID

日志格式：
```
INFO 2026-05-14 10:30:00 views [trace_id=abc123... span_id=def456...] Processing trace list request
```

---

## 6. 配置参考

### 6.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OTEL_ENABLED` | `false` | 总开关。设为 `true` / `1` / `yes` 启用 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP 基础地址，部署时按实际服务覆盖 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `{ENDPOINT}/v1/traces` | Traces 上报地址（可单独覆盖） |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `{ENDPOINT}/v1/metrics` | Metrics 上报地址（可单独覆盖） |
| `OTEL_EXPORTER_OTLP_HEADERS` | 空 | 受保护的 Collector 所需请求头，由部署配置提供 |
| `OTEL_SERVICE_NAME` | `aivision-backend` | 服务名（Resource attribute） |
| `APP_VERSION` | `dev` | 服务版本 |
| `AIVISION_ENV` | `development` | 部署环境（development/prepub/gray/prod） |
| `OTEL_PYTHON_DJANGO_EXCLUDED_URLS` | - | 排除的 URL 正则（逗号分隔） |

### 6.2 各环境推荐配置

#### 开发环境（`.env.development`）

```bash
OTEL_ENABLED=false
```

默认关闭，需要本地调试时手动开启。

#### 预发环境（`.env.prepub`）

```bash
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector-host>:4317
OTEL_PYTHON_DJANGO_EXCLUDED_URLS=health,static,favicon
```

#### 生产环境（`.env.prod`）

```bash
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector-host>:4317
OTEL_PYTHON_DJANGO_EXCLUDED_URLS=health,static,favicon
```

---

## 7. 使用指南

### 7.1 本地开发验证

**方式一：使用 Docker Compose 启动 OTel Collector**

```bash
# 1. 创建 Collector 配置
cat > /tmp/otel-collector-config.yaml <<'EOF'
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
exporters:
  debug:
    verbosity: detailed
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
    metrics:
      receivers: [otlp]
      exporters: [debug]
EOF

# 2. 启动 Collector
docker run --rm -p 4317:4317 \
  -v /tmp/otel-collector-config.yaml:/etc/otelcol/config.yaml \
  otel/opentelemetry-collector:latest

# 3. 启动 Django（另一个终端）
cd aivision-py-be
OTEL_ENABLED=true uv run python manage.py runserver

# 4. 发送请求
curl http://localhost:8000/api/v1/dashboard/summary

# 5. 在 Collector 终端查看输出的 traces 和 metrics
```

**方式二：使用 Console Exporter（快速验证）**

临时修改 `observability.py` 中的 exporter 为 `ConsoleSpanExporter` / `ConsoleMetricExporter`，traces 和 metrics 会直接打印到 stdout。

### 7.2 添加新的自定义指标

```python
# 1. 在 apps/core/metrics.py 中定义
from opentelemetry import metrics

meter = metrics.get_meter("aivision.core")

my_new_counter = meter.create_counter(
    name="aivision.my_feature.count",
    description="Number of my feature invocations",
)

my_new_histogram = meter.create_histogram(
    name="aivision.my_feature.duration",
    description="My feature processing duration",
    unit="s",
)

# 2. 在业务代码中使用
import time
from apps.core.metrics import my_new_counter, my_new_histogram

start = time.monotonic()
try:
    do_something()
    my_new_counter.add(1, {"variant": "a", "status": "success"})
except Exception:
    my_new_counter.add(1, {"variant": "a", "status": "error"})
    raise
finally:
    my_new_histogram.record(time.monotonic() - start, {"variant": "a"})
```

### 7.3 查看 Trace ID 关联日志

当 OTel 启用时，日志输出自动包含 trace context：

```
INFO 2026-05-14 10:30:00 views [trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7] ...
```

在 OTel Collector 或 Jaeger 中搜索同一 `trace_id`，即可关联该请求的完整调用链。

---

## 8. 运维与排障

### 8.1 开关不生效

确认 `OTEL_ENABLED` 值为 `true`、`1` 或 `yes`（大小写不敏感）。检查是否在正确的 `.env` 文件中设置。

### 8.2 Collector 收不到数据

1. 确认 Collector 监听地址与 `OTEL_EXPORTER_OTLP_ENDPOINT` 一致
2. 确认网络可达：`telnet <collector-host> 4317`
3. 查看 Django 日志中是否有 `Failed to initialize OpenTelemetry` 错误
4. SDK 默认静默失败（exporter 连接失败不会 crash），需检查 OTel SDK 日志级别

### 8.3 性能影响

- **关闭状态**：`init_observability()` 直接返回；`metrics.py` 中的 instruments 使用 no-op provider，`.record()` / `.add()` 为空操作
- **开启状态**：
  - BatchSpanProcessor 异步批量发送 traces，对请求延迟影响 < 1ms
  - PeriodicExportingMetricReader 每 60s 导出一次聚合指标，内存占用极小
  - Requests Instrumentor 对每个外部 HTTP 调用增加约 0.1ms overhead

### 8.4 日志格式兼容

日志格式中的 `otelTraceID` / `otelSpanID` 通过 formatter 的 `defaults` 参数设置了默认值 `"0"`，当 OTel 未启用时不会因缺少字段而报错。

---

## 9. 后续演进

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| **Phase 2** | DB 查询插桩（`opentelemetry-instrumentation-mysql`） | 中 |
| **Phase 2** | Gunicorn worker 指标（活跃 worker 数、请求队列深度） | 中 |
| **Phase 3** | 前端 RUM（Real User Monitoring）接入 OTel Web SDK | 低 |
| **Phase 3** | 基于 OTel 指标的告警规则（错误率、延迟 P99） | 高 |
| **Phase 4** | ~~Grafana Dashboard 模板（预置 HTTP、Trace Query、Adapter 面板）~~ ✅ 已完成 | 中 |
| **Phase 4** | SLO/SLI 定义与错误预算追踪 | 低 |

---

## Grafana 自观测大盘

可将下面的 JSON 导入自己的 Grafana，并配置对应的指标数据源。

**Dashboard JSON 源文件**：`docs/observability/grafana-dashboard.json`

### 大盘结构

| Row | 内容 | 面板 |
|-----|------|------|
| 1 - 概览 HTTP Server | 请求量、错误率、延迟概览 | 当前 QPM、错误率 (5xx)、P95/P99 延迟、QPM 趋势（按路由）、错误请求趋势、延迟百分位数、每类 URL P95 耗时 |
| 2 - 业务指标 Trace 查询 | Trace 查询性能 | Trace 查询 QPM、错误率、P95 延迟、认证失败速率、按 Provider 耗时/次数 |
| 3 - 外部依赖 HTTP Client | 外部调用监控 | 外部调用 QPM/延迟 P95/错误率（按目标服务） |
| 4 - 数据适配器 | Adapter 性能 | 执行耗时 P95、错误数（按类型） |
| 5 - 认证 | Auth 事件 | 认证事件趋势、认证失败率 |
| 6 - 详细指标 | 接口级别排行 | Top 10 最慢接口、Top 10 高流量接口 |

### 关键配置

- **数据源**：AntMonitor 云原生 (`dsId: 1400087`, `namespace: aivision_product`)
- **语义规范**：`OTEL_SEMCONV_STABILITY_OPT_IN=http`（新 HTTP semconv，指标名 `http.server.request.duration`）
- **粒度**：分钟级（QPM，默认 interval=1m）
- **变量**：`service_name`（通过 `query_result` 提取）、`interval`（1m/2m/5m/10m/30m）

---

## 附录：文件清单

| 文件路径 | 类型 | 说明 |
|----------|------|------|
| `aivision_backend/observability.py` | 新建 | OTel SDK 初始化入口 |
| `apps/core/metrics.py` | 新建 | 自定义业务指标定义 |
| `apps/core/apps.py` | 修改 | `ready()` 中调用初始化 |
| `apps/core/trace_query/service.py` | 修改 | Trace 查询埋点 |
| `apps/core/adapters/base.py` | 修改 | 数据适配器埋点 |
| `apps/core/authentication.py` | 修改 | 认证事件埋点 |
| `aivision_backend/settings.py` | 修改 | 日志格式加 trace context |
| `pyproject.toml` | 修改 | OTel 依赖 |
