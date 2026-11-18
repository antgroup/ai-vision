"""
OpenTelemetry observability bootstrap for AI Vision backend.

Controlled by the ``OTEL_ENABLED`` environment variable (default **off**).
When enabled, configures:

* **TracerProvider** with OTLP HTTP exporter – produces distributed traces
  for every incoming Django request and outgoing ``requests`` call.
* **MeterProvider** with OTLP HTTP exporter – produces HTTP server/client
  metrics plus custom business metrics defined in ``apps.core.metrics``.
* **LoggingInstrumentor** – injects ``otelTraceID`` / ``otelSpanID`` into
  every stdlib ``logging`` record for log-trace correlation.

Standard OTel environment variables (``OTEL_EXPORTER_OTLP_ENDPOINT``,
``OTEL_SERVICE_NAME``, …) are respected by the SDK automatically.
"""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_observability() -> None:
    """Initialize OpenTelemetry SDK if ``OTEL_ENABLED`` is truthy.

    Safe to call multiple times – only the first invocation takes effect.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    if os.environ.get("OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return

    # Use new HTTP semantic conventions (http.server.request.duration instead
    # of http.server.duration) for consistent metric naming across the stack.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    try:
        _setup()
        logger.info("OpenTelemetry observability initialized")
    except Exception:
        logger.exception("Failed to initialize OpenTelemetry – continuing without observability")


def _setup() -> None:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _COLLECTOR_BASE = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://localhost:4318",
    )

    import socket

    _hostname = socket.gethostname()
    _instance_id = os.environ.get("POD_NAME", _hostname)

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "aivision-backend"),
            "service.version": os.environ.get("APP_VERSION", "dev"),
            "deployment.environment": os.environ.get(
                "AIVISION_ENV", "development"
            ),
            "host.name": _hostname,
            "service.instance.id": _instance_id,
        }
    )

    # ── Traces ───────────────────────────────────────────────────────────
    traces_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        f"{_COLLECTOR_BASE}/v1/traces",
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
    )
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ──────────────────────────────────────────────────────────
    metrics_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        f"{_COLLECTOR_BASE}/v1/metrics",
    )
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metrics_endpoint),
        export_interval_millis=60_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # ── Auto-instrumentation ─────────────────────────────────────────────
    DjangoInstrumentor().instrument(
        is_sql_commentor_enabled=True,
        excluded_urls="health,static/.*,favicon.ico",
    )
    RequestsInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)
