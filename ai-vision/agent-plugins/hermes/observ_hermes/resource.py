"""Production configuration adapter for the shared Hermes Resource projection."""

from __future__ import annotations

from typing import Any

from aivision_hermes_core.resource import (
    build_resource_attributes as build_core_resource,
)

from .config import ObservHermesConfig


def build_resource_attributes(config: ObservHermesConfig) -> dict[str, Any]:
    return build_core_resource(
        service_name=config.service_name or "hermes",
        agent_type=config.ant.agent_type or "hermes",
        agent_product=config.ant.agent_product or "Hermes",
        agent_instance=config.ant.agent_instance or "unknown",
        agent_env=config.ant.agent_env or "Default",
        agent_scene=config.ant.agent_scene or "default",
        collection_mode=config.collection_mode,
        configured=config.otel_exporter.resource_attributes,
    )
