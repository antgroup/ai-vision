from __future__ import annotations

from apps.core.trace_query.langfuse.provider import LangfuseTraceProvider
from apps.core.trace_query.service import TraceQueryService
from apps.core.trace_query.signoz.provider import SignozTraceProvider


_default_trace_query_service: TraceQueryService | None = None


def get_default_trace_query_service() -> TraceQueryService:
    global _default_trace_query_service
    if _default_trace_query_service is None:
        service = TraceQueryService()
        service.register_provider(
            "langfuse",
            LangfuseTraceProvider(),
            source_systems=("langfuse",),
        )
        service.register_provider(
            "signoz",
            SignozTraceProvider(),
            source_systems=("signoz",),
        )
        _default_trace_query_service = service
    return _default_trace_query_service
