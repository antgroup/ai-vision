"""Materialize runtime-specific semantic facts onto a Trace."""

from __future__ import annotations

from agentlens.service.datasource.projection.contracts import ProjectionInputError
from agentlens.service.datasource.projection.models import Projection
from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.trace import Trace


def project_trace(
    trace: Trace,
    *,
    require_text_evidence: bool = True,
) -> Trace:
    """Return the Trace enriched with one runtime-owned Projection.

    Unknown runtimes and projection failures remain ordinary Trace values with
    unchanged source spans, ``projection=None``, and a diagnostic error string.
    """
    if trace.projection is not None or trace.projection_error is not None:
        return trace
    projector = get_dedicated_projector(trace.runtime_type)
    if projector is None:
        return trace.with_projection(
            None,
            error=f"runtime_type {trace.runtime_type!r} has no projector",
        )
    if not trace.spans:
        return trace.with_projection(
            None, error="trace has no source spans", failure="source_spans_empty"
        )
    try:
        projection = projector.project(trace.spans, trace.trace_id)
    except ProjectionInputError as exc:
        # Retain the historical diagnostic string independently of the safe cause.
        return trace.with_projection(None, error=f"ValueError: {exc}", failure=exc.cause)
    except Exception as exc:  # noqa: BLE001
        return trace.with_projection(
            None,
            error=f"{type(exc).__name__}: {exc}",
        )
    if projection is None or not isinstance(projection, Projection):
        return trace.with_projection(
            None,
            error=f"trace not projectable into runtime_type={trace.runtime_type!r}",
        )
    return trace.with_projection(projection)
