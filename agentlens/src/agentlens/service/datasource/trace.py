"""Unified Trace entity produced by data sources and consumed by Diagnosers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from agentlens.domain.enums import DatasetType

if TYPE_CHECKING:
    from agentlens.service.datasource.projection.models import Projection


class ProjectionUnavailable(RuntimeError):
    """The Trace has no runtime semantic projection."""

    def __init__(self, message: str = "", *, cause: str | None = None):
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class Trace:
    """One complete trace with unchanged source spans and optional semantics."""

    trace_id: str
    runtime_type: str
    spans: list[dict[str, Any]]
    owner_id: str | None = None
    source_type: DatasetType | None = None
    source_meta: dict[str, Any] = field(default_factory=dict)
    projection: Projection | None = None
    projection_error: str | None = None
    projection_failure: str | None = None

    @property
    def timestamp(self) -> str | None:
        return self.projection.timestamp if self.projection is not None else None

    def with_projection(
        self,
        projection: Projection | None,
        *,
        error: str | None = None,
        failure: str | None = None,
    ) -> Trace:
        return replace(
            self,
            projection=projection,
            projection_error=error,
            projection_failure=failure,
        )

    def require_projection(self) -> Projection:
        if self.projection is None:
            raise ProjectionUnavailable(
                self.projection_error or f"runtime_type {self.runtime_type!r} has no projection",
                cause=self.projection_failure,
            )
        return self.projection

    def to_python_value(self) -> dict[str, Any]:
        """Expose the stable custom Python/LLM value contract."""
        if self.projection is not None:
            from agentlens.service.datasource.projection.serialization import (
                serialize_trace_projection,
            )

            data = serialize_trace_projection(self)
        else:
            data = {
                "trace_id": self.trace_id,
                "timestamp": None,
                "runtime_type": self.runtime_type,
            }
        data["spans"] = self.spans
        return data
