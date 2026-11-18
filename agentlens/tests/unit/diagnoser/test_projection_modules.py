"""Structural contract for projection modules."""

import agentlens.service.datasource.projection as projection
from agentlens.service.datasource.projection import models, registry, service
from agentlens.service.datasource.projection.projectors.coding.projector import CodingProjector
from agentlens.service.datasource.projection.projectors.framework.projector import (
    FrameworkProjector,
)


def test_projection_public_interface_remains_small():
    expected = {
        "CodingProjection",
        "Projection",
        "project_trace",
        "get_dedicated_projector",
    }
    assert set(projection.__all__) == expected


def test_projection_modules_are_wired():
    assert models.Projection is projection.Projection
    assert [definition.projector_type for definition in registry.BUILTIN_PROJECTORS] == [
        FrameworkProjector,
        CodingProjector,
    ]
    assert registry.get_dedicated_projector is projection.get_dedicated_projector
    assert service.project_trace is projection.project_trace
