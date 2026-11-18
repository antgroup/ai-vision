"""Framework facts reuse sourced tool execution, not Coding diagnosis scope."""

from dataclasses import dataclass

from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection


@dataclass(kw_only=True)
class FrameworkProjection(CodingProjection):
    pass
