from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


TRACE_TEXT_SEARCH_NOT_CONFIGURED_CODE = 3034
TRACE_TEXT_SEARCH_QUERY_FAILED_CODE = 3035


class TraceQueryServiceError(Exception):
    """Base exception for trace query service failures."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class TraceProviderNotConfiguredError(TraceQueryServiceError):
    """Raised when no trace backend is available for the target space."""


class TraceProvider(Protocol):
    """Common interface for trace backends."""

    def list_traces(self, query: "TraceListQuery") -> dict[str, Any]:
        ...

    def get_trace_io_batch(self, query: "TraceIOBatchQuery") -> dict[str, Any]:
        ...

    def get_trace_usage_batch(self, query: "TraceUsageBatchQuery") -> dict[str, Any]:
        ...

    def get_trace_detail(self, query: "TraceDetailQuery") -> dict[str, Any]:
        ...

    def list_memory_spans(self, query: "TraceListQuery") -> dict[str, Any]:
        ...

    def list_sessions(self, query: "SessionListQuery") -> dict[str, Any]:
        ...

    def get_session_detail(self, query: "SessionDetailQuery") -> dict[str, Any]:
        ...

    def get_session_io_batch(self, query: "SessionIOBatchQuery") -> dict[str, Any]:
        ...

    def get_session_io(self, query: "SessionIOQuery") -> dict[str, Any]:
        ...

    def get_session_usage_batch(self, query: "SessionUsageBatchQuery") -> dict[str, Any]:
        ...

    def get_span_batch(self, query: "SpanBatchQuery") -> dict[str, Any]:
        ...

    def get_observation_detail(self, query: "ObservationDetailQuery") -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class BaseTraceQuery:
    space: Any
    user: Any
    query_params: Mapping[str, Any]

    def get_param(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.query_params, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    @property
    def agent_product(self) -> str | None:
        value = self.get_param("agent_product")
        return value if isinstance(value, str) else None

    @property
    def agent_uuid(self) -> str | None:
        value = self.get_param("agent_uuid")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class TraceListQuery(BaseTraceQuery):
    pass


@dataclass(frozen=True)
class TraceIOBatchQuery(BaseTraceQuery):
    trace_ids: list[str]


@dataclass(frozen=True)
class TraceUsageBatchQuery(BaseTraceQuery):
    trace_ids: list[str]


@dataclass(frozen=True)
class TraceDetailQuery(BaseTraceQuery):
    trace_id: str


@dataclass(frozen=True)
class SessionListQuery(BaseTraceQuery):
    pass


@dataclass(frozen=True)
class SessionDetailQuery(BaseTraceQuery):
    session_id: str


@dataclass(frozen=True)
class SessionIOBatchQuery(BaseTraceQuery):
    session_ids: list[str]


@dataclass(frozen=True)
class SessionIOQuery(BaseTraceQuery):
    session_id: str


@dataclass(frozen=True)
class SessionUsageBatchQuery(BaseTraceQuery):
    session_ids: list[str]


@dataclass(frozen=True)
class SpanBatchQuery(BaseTraceQuery):
    trace_id: str
    span_ids: list[str]


@dataclass(frozen=True)
class ObservationDetailQuery(BaseTraceQuery):
    observation_id: str
    trace_id: str | None = None
