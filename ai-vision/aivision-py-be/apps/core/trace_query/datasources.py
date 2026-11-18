from __future__ import annotations

from typing import Any

def get_trace_datasource(space: Any, agent_product: str | None = None) -> Any | None:
    from apps.core.trace_query.routing import get_personal_trace_datasource

    return get_personal_trace_datasource(space, agent_product=agent_product)


def list_space_agent_products(space: Any, agent_product: str | None = None) -> list[str]:
    from apps.core.trace_query.routing import list_space_agent_products as _list

    return _list(space, agent_product)
