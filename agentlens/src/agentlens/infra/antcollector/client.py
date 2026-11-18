from collections import defaultdict
from datetime import datetime
from typing import Any, Protocol

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from agentlens.logging import get_logger


class AntcollectorClient(Protocol):
    def write_span(
        self,
        table: str,
        name: str,
        attributes: dict[str, Any],
        body: dict[str, Any],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None: ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...

    def close(self) -> None: ...


class InMemoryAntcollectorClient:
    """单测/本地开发用的内存实现。可传入共享 store 与 InMemorySignozClient 联动。"""

    def __init__(self, store: dict[str, list[dict]] | None = None) -> None:
        self._tables: dict[str, list[dict]] = store if store is not None else defaultdict(list)

    def write_span(
        self,
        table: str,
        name: str,
        attributes: dict[str, Any],
        body: dict[str, Any],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        import json

        started_at = start_time or datetime.now()
        ended_at = end_time or datetime.now()
        row = {f"attributes.{key}": value for key, value in attributes.items()}
        row["name"] = name
        row["period"] = started_at.isoformat()
        row["start_time_unix_nano"] = started_at.isoformat()
        row["end_time_unix_nano"] = ended_at.isoformat()
        row["attributes.record.body"] = json.dumps(body, default=str, ensure_ascii=False)
        self._tables.setdefault(table, []).append(row)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def close(self) -> None:
        pass

    @property
    def records(self) -> dict[str, list[dict]]:
        return dict(self._tables)


class HttpAntcollectorClient:
    """通过 OTel Trace SDK 上报诊断记录到 Antcollector。"""

    def __init__(
        self,
        otlp_endpoint: str,
        access_id: str = "",
        access_token: str = "",
        forward_service: str = "",
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self.otlp_endpoint = otlp_endpoint.rstrip("/") if otlp_endpoint else ""
        self._log = get_logger("antcollector")
        self._tracer_provider: TracerProvider | None = None
        if self.otlp_endpoint:
            resource = Resource.create({"service.name": "agentlens"})
            headers: dict[str, str] = {}
            if access_id:
                headers["X-Access-Id"] = access_id
            if access_token:
                headers["X-Access-Token"] = access_token
            if forward_service:
                headers["x-antmonitor-forward-service"] = forward_service
            exporter = span_exporter or OTLPSpanExporter(
                endpoint=self.otlp_endpoint,
                headers=headers,
            )
            self._span_exporter = exporter
            self._tracer_provider = TracerProvider(resource=resource)
            self._tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
            self._tracer = self._tracer_provider.get_tracer("agentlens.records")

    def write_span(
        self,
        table: str,
        name: str,
        attributes: dict[str, Any],
        body: dict[str, Any],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        import json

        if not self.otlp_endpoint:
            self._log.info(
                "antcollector.write_dry_run",
                table=table,
                name=name,
                uuid=str(attributes.get("uuid", "")),
                task_uuid=str(attributes.get("task_uuid", "")),
                job_uuid=str(attributes.get("job_uuid", "")),
                attempt=attributes.get("attempt", 0),
            )
            return
        span_attributes = attributes
        serialized_body = json.dumps(body, default=str, ensure_ascii=False)
        if start_time is None and end_time is None:
            with self._tracer.start_as_current_span(
                name=name,
                attributes=span_attributes,
            ) as span:
                span.set_attribute("record.body", serialized_body)
        else:
            start_ns = int(start_time.timestamp() * 1_000_000_000) if start_time else None
            end_ns = int(end_time.timestamp() * 1_000_000_000) if end_time else None
            span = self._tracer.start_span(
                name=name,
                attributes=span_attributes,
                start_time=start_ns,
            )
            try:
                span.set_attribute("record.body", serialized_body)
            finally:
                span.end(end_time=end_ns)
        self._log.debug(
            "antcollector.write_enqueued",
            table=table,
            name=name,
            uuid=attributes.get("uuid", ""),
            task_uuid=attributes.get("task_uuid", ""),
        )

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        provider = self._tracer_provider
        if provider is None:
            return True
        flushed = bool(provider.force_flush(timeout_millis=timeout_millis))
        exporter = getattr(self, "_span_exporter", None)
        return flushed and (exporter is None or exporter.force_flush(timeout_millis))

    def close(self) -> None:
        provider = self._tracer_provider
        if provider:
            provider.shutdown()
            self._tracer_provider = None
