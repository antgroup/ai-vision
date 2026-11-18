"""Projector implementation contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentlens.service.datasource.projection.models import Projection


class ProjectionInputError(ValueError):
    """A known input failure with a safe, structural cause identifier."""

    def __init__(self, message: str, *, cause: str):
        super().__init__(message)
        self.cause = cause


class Projector(ABC):
    """Build runtime-owned semantics from unchanged grouped source rows."""

    canonical_runtime: str
    available_vars: dict[str, str]
    available_image_vars: dict[str, str] = {}

    @property
    def runtime_type(self) -> str:
        """Compatibility name for the canonical runtime."""
        return self.canonical_runtime

    @abstractmethod
    def project(self, raw_rows: list[dict[str, Any]], trace_id: str) -> Projection | None:
        """Convert raw rows into runtime-specific diagnosis semantics."""

    @abstractmethod
    def resolve_var(self, trace: Any, var: str) -> Any:
        """Resolve one runtime-owned semantic variable."""

    def resolve_image_var(self, trace: Any, var: str) -> Any:
        """Resolve one registered internal image variable."""
        return getattr(trace, var, None)
