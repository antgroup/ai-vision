"""Build a production-equivalent Trace for editor trials."""

from __future__ import annotations

from typing import Any

from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace


def project_trial_input(test_input: dict[str, Any]) -> Trace:
    """Return unchanged spans plus optional dedicated Projection."""
    runtime_type = str(test_input.get("runtime_type") or "other")
    spans = test_input.get("spans", [])
    if not isinstance(spans, list) or any(not isinstance(span, dict) for span in spans):
        raise ValueError("test_input.spans 必须是对象数组")
    trace_id = str(test_input.get("trace_id") or "trial-trace")
    return project_trace(
        Trace(
            trace_id=trace_id,
            runtime_type=runtime_type,
            spans=spans,
        )
    )
