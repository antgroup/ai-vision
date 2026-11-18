"""Runtime-type registry validation (separate from the projection public interface).

`validate_runtime_type` is a configuration-validation helper used by the
Dataset/Job/trace routes and the runner. It lives outside the `projection`
package's public surface so projection does not grow a configuration-validation
responsibility. A runtime only needs a non-empty name; projector availability is
resolved later when a Trace is evaluated.
"""

from __future__ import annotations

from typing import Any

from agentlens.domain.errors import Validation


def validate_runtime_type(runtime_type: Any) -> str:
    """Require a non-empty runtime_type without requiring a Projector."""
    if not isinstance(runtime_type, str) or not runtime_type.strip():
        raise Validation("runtime_type 不能为空")
    return runtime_type


def supports_specialist_runtime(runtime_type: str, *, key: str | None = None) -> bool:
    """Specialist materials require the existing Coding or Framework fact mapping."""
    from agentlens.service.datasource.projection.registry import get_dedicated_projector

    projector = get_dedicated_projector(runtime_type)
    if projector is None:
        return False
    if key == "required_tool_execution":
        return projector.canonical_runtime == "langchain"
    return projector.canonical_runtime in {"cc", "langchain"}
