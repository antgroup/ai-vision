from __future__ import annotations

from typing import Any

from apps.core.services.agent_routing import (
    list_routable_space_agents,
    resolve_routable_space_agent,
    resolve_space_agent_for_query,
)
from apps.core.trace_query.types import TraceProviderNotConfiguredError

EVENT_SOURCE_SYSTEMS = ("signoz",)
# Pending Agents need read access to verify ingestion before becoming Normal.
# Disabled Agents remain excluded, matching the trace query lifecycle.
EVENT_QUERY_ACTIVE_AGENT_STATUSES = (1, 2)


def _select_event_route_record(records: list[Any], *, error_message: str) -> Any | None:
    if not records:
        return None
    default_records = [
        record for record in records if getattr(record, "is_default", 0) == 1
    ]
    candidates = default_records or records
    if len(candidates) != 1:
        raise TraceProviderNotConfiguredError(error_message, 3011)
    return candidates[0]


def get_event_datasource(
    space: Any,
    *,
    agent_product: str | None = None,
    agent_uuid: Any = None,
    source_system: str | None = None,
) -> Any | None:
    from apps.core.models import DataSource

    if agent_product:
        if agent_uuid:
            agent = resolve_space_agent_for_query(
                space, agent_product, agent_uuid=agent_uuid,
                active_statuses=EVENT_QUERY_ACTIVE_AGENT_STATUSES,
            )
        else:
            agent = resolve_routable_space_agent(
                space, agent_product, active_statuses=EVENT_QUERY_ACTIVE_AGENT_STATUSES
            )
        if not agent:
            raise TraceProviderNotConfiguredError(
                "Agent query route not found",
                3011,
            )
        agents = [agent]
    else:
        agents = list_routable_space_agents(
            space, active_statuses=EVENT_QUERY_ACTIVE_AGENT_STATUSES
        )
        if not agents:
            return None
    agent_type_ids = list(
        {agent.agent_type_id for agent in agents if agent.agent_type_id is not None}
    )
    queryset = DataSource.objects.select_related("agent_type").filter(
        datasource_type="logs",
        source_system__in=EVENT_SOURCE_SYSTEMS,
        enabled=1,
        gmt_deleted__isnull=True,
        agent_type_id__in=agent_type_ids,
        agent_type__status=1,
        agent_type__gmt_deleted__isnull=True,
        agent_type__agents__id__in=[agent.id for agent in agents],
        agent_type__agents__space=space,
        agent_type__agents__status__in=EVENT_QUERY_ACTIVE_AGENT_STATUSES,
        agent_type__agents__gmt_deleted__isnull=True,
    )
    eligible_queryset = queryset
    if source_system:
        queryset = queryset.filter(source_system=source_system)
    datasource = _select_event_route_record(
        list(queryset.order_by("-is_default", "id").distinct()),
        error_message=(
            "Multiple event log backends configured for this space; "
            "please select agent_product or set exactly one default route"
        ),
    )
    if agent_product and not datasource:
        if source_system and eligible_queryset.exists():
            return None
        raise TraceProviderNotConfiguredError(
            "Agent query route not found",
            3011,
        )
    return datasource
