"""Shared Hermes Resource projection without product or transport dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import __version__ as PACKAGE_VERSION
from .privacy import hide_content, sanitize

_SIGNAL_IDENTITY_KEYS = frozenset({"ant.username", "ant.uid", "user.id", "username"})


def build_resource_attributes(
    *,
    service_name: str = "hermes",
    service_version: str = PACKAGE_VERSION,
    agent_type: str = "hermes",
    agent_product: str = "Hermes",
    agent_instance: str = "unknown",
    agent_env: str = "Default",
    agent_scene: str = "default",
    collection_mode: str = "full",
    configured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical nine-field Hermes Resource plus safe configured fields."""
    sanitized = sanitize(dict(configured or {})).value
    configured_attributes = {
        key: value
        for key, value in sanitized.items()
        if key not in _SIGNAL_IDENTITY_KEYS
    }

    def safe_text(value: Any) -> str:
        return str(sanitize(str(value)).value)

    if collection_mode == "privacy":
        configured_attributes = {
            key: hide_content(sanitize(value).value).value
            for key, value in configured_attributes.items()
        }
        service_name = hide_content(safe_text(service_name)).value
        agent_product = hide_content(safe_text(agent_product)).value
        agent_instance = hide_content(safe_text(agent_instance)).value
        agent_env = hide_content(safe_text(agent_env)).value
        agent_scene = hide_content(safe_text(agent_scene)).value
        agent_type = "hermes"
    else:
        service_name = safe_text(service_name)
        agent_product = safe_text(agent_product)
        agent_instance = safe_text(agent_instance)
        agent_env = safe_text(agent_env)
        agent_scene = safe_text(agent_scene)
        agent_type = safe_text(agent_type)

    return {
        **configured_attributes,
        "service.name": service_name,
        "service.version": safe_text(service_version),
        "ant.agent.type": agent_type,
        "ant.agent.env": agent_env,
        "deployment.environment": agent_env,
        "ant.agent.scene": agent_scene,
        "agentic.collection.mode": collection_mode,
        "ant.agent.instance": agent_instance,
        "ant.agent.product": agent_product,
    }
