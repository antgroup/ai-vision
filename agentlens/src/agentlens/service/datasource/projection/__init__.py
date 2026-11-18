"""Diagnosis-ready typed Projection public interface."""

from agentlens.service.datasource.projection.models import Projection
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection
from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.projection.service import project_trace

__all__ = [
    "CodingProjection",
    "Projection",
    "project_trace",
    "get_dedicated_projector",
]
