"""Production OTLP sinks for canonical semantic observations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aivision_hermes_core import __version__ as PACKAGE_VERSION
from aivision_hermes_core.privacy import hide_content, sanitize
from aivision_hermes_core.semantic import SemanticObservation
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NonRecordingSpan, SpanKind, Status, StatusCode

from .config import ObservHermesConfig
from .exporters.http_json import HttpJsonSink
from .metrics import MetricsRuntime, create_metrics
from .resource import build_resource_attributes

CONTENT_ATTRIBUTE_FRAGMENTS = (
    "input.messages",
    "output.messages",
    "tool.call.arguments",
    "tool.call.result",
    "error.message",
    "child.goal",
    "child.summary",
    "prompt",
)


def _timestamp_nano(value: str) -> int:
    return int(
        datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000_000
    )


def normalize_signal_endpoint(endpoint: str, signal: str) -> str:
    """Append one standard OTLP signal path unless it is already qualified."""
    if signal not in {"traces", "metrics", "logs"}:
        raise ValueError(f"unsupported OTLP signal: {signal}")
    if not endpoint:
        return ""
    parsed = urlsplit(endpoint.strip())
    path = parsed.path.rstrip("/")
    suffix = f"/v1/{signal}"
    if not path.endswith(suffix):
        path = f"{path}{suffix}" if path else suffix
    return urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def _signal_endpoint(
    config: ObservHermesConfig, signal: str
) -> tuple[str, dict[str, str]]:
    signal_config = {
        "traces": config.traces_config,
        "metrics": config.metrics_config,
        "logs": config.events_config,
    }[signal]
    endpoint = signal_config.endpoint or config.otel_exporter.endpoint
    headers = {**config.otel_exporter.headers, **signal_config.headers}
    return normalize_signal_endpoint(endpoint, signal), headers


def resolve_signal_attributes(
    config: ObservHermesConfig,
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, Any]:
    """Resolve configured attributes and emission-time identity at one boundary."""
    env = os.environ if environ is None else environ
    sanitized = sanitize(config.otel_exporter.attributes).value
    attributes = dict(sanitized)
    if config.collection_mode == "privacy":
        attributes = {
            key: hide_content(value).value
            for key, value in attributes.items()
            if key not in {"ant.username", "ant.uid", "user.id"}
        }

    configured_username = str(
        sanitized.get("ant.username") or sanitize(config.ant.username).value or ""
    )
    configured_uid = str(
        sanitized.get("ant.uid")
        or sanitize(config.ant.uid).value
        or configured_username
    )
    username_raw = (
        env.get("AIVISION_ANT_USERNAME", "").strip()
        if config.dynamic_username
        else configured_username
    ) or configured_username
    uid_raw = (
        env.get("AIVISION_ANT_UID", "").strip()
        if config.dynamic_uid
        else configured_uid
    ) or configured_uid
    username = str(sanitize(username_raw).value)
    uid = str(sanitize(uid_raw).value)
    if username:
        attributes["ant.username"] = username
    if uid:
        attributes["ant.uid"] = uid
        attributes["user.id"] = uid
    return attributes


def project_observation(
    config: ObservHermesConfig,
    observation: SemanticObservation,
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> SemanticObservation:
    configured = resolve_signal_attributes(config, environ)
    projected: dict[str, Any] = {}
    for key, value in observation.attributes.items():
        sanitized = sanitize(value).value
        if config.collection_mode == "privacy" and any(
            fragment in key.casefold() for fragment in CONTENT_ATTRIBUTE_FRAGMENTS
        ):
            sanitized = hide_content(value).value
        projected[key] = sanitized
    combined = {**configured, **projected}
    for identity_key in ("ant.username", "ant.uid", "user.id"):
        if identity_key in configured:
            combined[identity_key] = configured[identity_key]
    return replace(observation, attributes=combined)


def _build_resource(config: ObservHermesConfig) -> Resource:
    return Resource.create(build_resource_attributes(config))


class OtelSemanticSink:
    def __init__(
        self, tracer: Any, event_logger: Any, metrics: MetricsRuntime | None
    ) -> None:
        self.tracer = tracer
        self.event_logger = event_logger
        self.metrics = metrics
        self._spans: dict[str, Any] = {}
        self._contexts: dict[str, Any] = {}

    def __call__(self, observation: SemanticObservation) -> None:
        if observation.type == "span.start" and observation.key:
            key = observation.key
            parent_context = self._context(observation.parent_key)
            kinds: dict[str, SpanKind] = {
                "CHAT": SpanKind.SERVER,
                "LLM": SpanKind.CLIENT,
                "TOOL": SpanKind.INTERNAL,
                "AGENT": SpanKind.INTERNAL,
            }
            kind = kinds.get(observation.span_kind or "", SpanKind.INTERNAL)
            span = self.tracer.start_span(
                observation.name,
                context=parent_context,
                kind=kind,
                attributes=observation.attributes,
                start_time=_timestamp_nano(observation.timestamp),
            )
            self._spans[key] = span
            self._contexts[key] = span.get_span_context()
            return
        if observation.type == "span.end" and observation.key:
            span = self._spans.pop(observation.key, None)
            if span is not None:
                for key, value in observation.attributes.items():
                    span.set_attribute(key, value)
                span.set_status(
                    Status(
                        StatusCode.ERROR
                        if observation.status == "ERROR"
                        else StatusCode.OK
                    )
                )
                span.end(end_time=_timestamp_nano(observation.timestamp))
            return
        if observation.type == "event" and self.event_logger is not None:
            self.event_logger.emit(
                timestamp=_timestamp_nano(observation.timestamp),
                observed_timestamp=_timestamp_nano(observation.timestamp),
                context=self._context(observation.key),
                severity_text="INFO",
                body={"event.name": observation.name},
                attributes=observation.attributes,
                event_name=observation.name,
            )
            return
        if observation.type == "metric" and self.metrics is not None:
            self.metrics.record(
                observation.name, observation.value or 0, observation.attributes
            )

    def abandon_pending(self) -> None:
        """Forget incomplete SDK spans without exporting fabricated terminals."""
        self._spans.clear()
        self._contexts.clear()

    def _context(self, key: str | None) -> Context | None:
        if not key:
            return None
        active = self._spans.get(key)
        if active is not None:
            return trace.set_span_in_context(active)
        span_context = self._contexts.get(key)
        if span_context is not None:
            return trace.set_span_in_context(NonRecordingSpan(span_context))
        return None


@dataclass
class TelemetryRuntime:
    tracer: Any
    provider: TracerProvider
    config: ObservHermesConfig
    otel_sink: OtelSemanticSink
    json_sink: HttpJsonSink | None = None
    metrics: MetricsRuntime | None = None
    event_logger: Any = None
    _meter_provider: Any = field(default=None, repr=False)
    _logger_provider: Any = field(default=None, repr=False)
    _shutdown: bool = field(default=False, repr=False)

    def consume_observation(self, observation: SemanticObservation) -> None:
        if self._shutdown:
            return
        observation = project_observation(self.config, observation)
        if self.json_sink is not None:
            self.json_sink(observation)
        else:
            self.otel_sink(observation)

    def force_flush(self) -> None:
        if self._shutdown:
            return
        self.provider.force_flush()
        if self._meter_provider is not None:
            self._meter_provider.force_flush()
        if self._logger_provider is not None:
            self._logger_provider.force_flush()
        if self.json_sink is not None:
            self.json_sink.force_flush()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        pipeline = getattr(self, "_observ_hermes_pipeline", None)
        if pipeline is not None:
            pipeline.close()
        if self.json_sink is not None:
            self.json_sink.shutdown()
        else:
            self.otel_sink.abandon_pending()
        self.force_flush()
        self.provider.shutdown()
        if self._meter_provider is not None:
            self._meter_provider.shutdown()
        if self._logger_provider is not None:
            self._logger_provider.shutdown()
        self._shutdown = True


def _init_traces(
    config: ObservHermesConfig, resource: Resource
) -> tuple[TracerProvider, Any]:
    provider = TracerProvider(resource=resource)
    if config.traces_enabled:
        endpoint, headers = _signal_endpoint(config, "traces")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint, headers=headers or {})
                )
            )
    return provider, provider.get_tracer("observ-hermes", PACKAGE_VERSION)


def _init_metrics(
    config: ObservHermesConfig, resource: Resource
) -> tuple[MetricsRuntime | None, Any]:
    if not config.metrics_enabled:
        return None, None
    endpoint, headers = _signal_endpoint(config, "metrics")
    if not endpoint:
        return None, None
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import (
        Counter,
        Histogram,
        MeterProvider,
        ObservableCounter,
        ObservableGauge,
        ObservableUpDownCounter,
        UpDownCounter,
    )
    from opentelemetry.sdk.metrics.export import (
        AggregationTemporality,
        PeriodicExportingMetricReader,
    )

    temporality = (
        AggregationTemporality.DELTA
        if config.otel_exporter.metrics_temporality_preference == "delta"
        else AggregationTemporality.CUMULATIVE
    )
    preferred_temporality = {
        instrument: temporality
        for instrument in (
            Counter,
            UpDownCounter,
            Histogram,
            ObservableCounter,
            ObservableUpDownCounter,
            ObservableGauge,
        )
    }
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=endpoint,
            headers=headers or {},
            preferred_temporality=preferred_temporality,
        ),
        export_interval_millis=config.metric_export_interval,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    return create_metrics(
        provider.get_meter("observ-hermes", PACKAGE_VERSION)
    ), provider


def _init_events(config: ObservHermesConfig, resource: Resource) -> tuple[Any, Any]:
    if not config.events_enabled:
        return None, None
    endpoint, headers = _signal_endpoint(config, "logs")
    if not endpoint:
        return None, None
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=endpoint, headers=headers or {}),
            max_export_batch_size=512,
            schedule_delay_millis=config.logs_export_interval,
        )
    )
    return provider.get_logger("observ-hermes", PACKAGE_VERSION), provider


def init_telemetry(config: ObservHermesConfig) -> TelemetryRuntime:
    resource = _build_resource(config)
    json_sink: HttpJsonSink | None = None
    if config.otel_exporter.protocol == "http/json":
        provider = TracerProvider(resource=resource)
        tracer = provider.get_tracer("observ-hermes", PACKAGE_VERSION)
        metrics = None
        meter_provider = None
        event_logger = None
        logger_provider = None
        endpoints: dict[str, str] = {}
        headers: dict[str, dict[str, str]] = {}
        for signal, enabled in (
            ("traces", config.traces_enabled),
            ("metrics", config.metrics_enabled),
            ("logs", config.events_enabled),
        ):
            if enabled:
                endpoint, signal_headers = _signal_endpoint(config, signal)
                if endpoint:
                    endpoints[signal] = endpoint
                    headers[signal] = signal_headers
        json_sink = HttpJsonSink(
            build_resource_attributes(config),
            endpoints,
            headers,
            aggregation_temporality=(
                1
                if config.otel_exporter.metrics_temporality_preference == "delta"
                else 2
            ),
        )
    else:
        provider, tracer = _init_traces(config, resource)
        metrics, meter_provider = _init_metrics(config, resource)
        event_logger, logger_provider = _init_events(config, resource)
    otel_sink = OtelSemanticSink(tracer, event_logger, metrics)
    return TelemetryRuntime(
        tracer=tracer,
        provider=provider,
        config=config,
        otel_sink=otel_sink,
        json_sink=json_sink,
        metrics=metrics,
        event_logger=event_logger,
        _meter_provider=meter_provider,
        _logger_provider=logger_provider,
    )
