"""Snapshots used by persisted diagnosis records."""

from typing import Any

from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.trace import Trace
from agentlens.service.datasource.variables import build_snapshot

_MAX_PROJECTION_ERROR_CHARS = 1024


def build_record_snapshot(trace: Trace, *, projection_failed: bool = False) -> dict[str, Any]:
    """Keep failed semantic records bounded without changing raw CUSTOM snapshots."""
    if (
        projection_failed
        and trace.projection is None
        and trace.projection_error
        and get_dedicated_projector(trace.runtime_type) is not None
    ):
        error = trace.projection_error
        if len(error) > _MAX_PROJECTION_ERROR_CHARS:
            error = error[:_MAX_PROJECTION_ERROR_CHARS] + "…(truncated)"
        return {
            "trace_id": trace.trace_id,
            "runtime_type": trace.runtime_type,
            "timestamp": None,
            "projection_error": error,
            "source_span_count": len(trace.spans),
            "spans_omitted": True,
        }
    return build_snapshot(trace)
