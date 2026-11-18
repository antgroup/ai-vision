"""Authenticate diagnostic OTLP batches using the persisted Job's scope."""

from collections import OrderedDict, defaultdict
from collections.abc import Sequence
from threading import Lock
from time import monotonic

import httpx
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.logging import get_logger

Scope = tuple[str, str]


class ScopedIngestExporter(SpanExporter):
    def __init__(self, *, endpoint, token_url, service_token, session_factory):
        self.endpoint = endpoint
        self.token_url = token_url
        self.service_token = service_token
        self.session_factory = session_factory
        self._tokens: OrderedDict[Scope, tuple[float, str]] = OrderedDict()
        self._lock = Lock()
        self._failed_since_flush = False
        self._log = get_logger("ingest_exporter")

    def _token(self, scope: Scope) -> str:
        started = monotonic()
        cached = self._tokens.get(scope)
        if cached and cached[0] > started:
            self._tokens.move_to_end(scope)
            return cached[1]
        response = httpx.post(
            self.token_url,
            headers={"x-antmonitor-token": self.service_token},
            json={"space_uuid": scope[0], "agent_uuid": scope[1]},
            timeout=10,
            follow_redirects=False,
        )
        response.raise_for_status()
        envelope = response.json()
        data = envelope.get("data") or {}
        if (
            envelope.get("code") != 0
            or data.get("space_id") != scope[0]
            or data.get("agent_uuid") != scope[1]
            or data.get("token_type") != "Bearer"
            or not isinstance(data.get("token"), str)
            or not data["token"]
            or not isinstance(data.get("expires_in"), (float, int))
            or data["expires_in"] <= 0
        ):
            raise ValueError("invalid scoped ingest credential")
        self._tokens[scope] = (started + max(0, data["expires_in"] - 60), data["token"])
        self._tokens.move_to_end(scope)
        if len(self._tokens) > 128:
            self._tokens.popitem(last=False)
        return data["token"]

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            result = self._export(spans)
            self._failed_since_flush |= result is SpanExportResult.FAILURE
            return result

    def _export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        groups: dict[Scope, list[ReadableSpan]] = defaultdict(list)
        try:
            # Resolve from AgentLens's own database, avoiding a callback into its
            # HTTP server while a Runner is synchronously flushing this exporter.
            with self.session_factory() as session:
                jobs = JobTable(session)
                scopes = {}
                for span in spans:
                    attrs = span.attributes or {}
                    job_uuid = attrs.get("job_uuid")
                    if not isinstance(job_uuid, str) or not job_uuid:
                        raise ValueError("diagnostic span requires job_uuid")
                    if job_uuid not in scopes:
                        job = jobs.get_by_uuid(job_uuid)
                        if job is None:
                            raise ValueError("diagnostic Job no longer exists")
                        scopes[job_uuid] = (job.space_uuid, job.agent_uuid)
                    scope = scopes[job_uuid]
                    if attrs.get("space_id") != scope[0]:
                        raise ValueError("diagnostic span does not match Job scope")
                    groups[scope].append(span)
        except Exception as exc:  # noqa: BLE001
            self._log.error("ingest.scope_failed", error_type=type(exc).__name__)
            return SpanExportResult.FAILURE

        result = SpanExportResult.SUCCESS
        for scope, batch in groups.items():
            exporter = None
            try:
                exporter = OTLPSpanExporter(
                    endpoint=self.endpoint,
                    headers={"Authorization": f"Bearer {self._token(scope)}"},
                    timeout=10,
                )
                if exporter.export(batch) is not SpanExportResult.SUCCESS:
                    raise RuntimeError("OTLP export failed")
            except Exception as exc:  # noqa: BLE001
                self._tokens.pop(scope, None)
                self._log.error(
                    "ingest.export_failed",
                    space_uuid=scope[0],
                    agent_uuid=scope[1],
                    error_type=type(exc).__name__,
                )
                result = SpanExportResult.FAILURE
            finally:
                if exporter is not None:
                    exporter.shutdown()
        return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            succeeded = not self._failed_since_flush
            self._failed_since_flush = False
            return succeeded

    def shutdown(self) -> None:
        with self._lock:
            self._tokens.clear()


def make_ingest_exporter(settings, session_factory) -> ScopedIngestExporter | None:
    if not settings.aivision_ingest_token_url:
        return None
    return ScopedIngestExporter(
        endpoint=settings.antcollector_url,
        token_url=settings.aivision_ingest_token_url,
        service_token=settings.token,
        session_factory=session_factory,
    )
