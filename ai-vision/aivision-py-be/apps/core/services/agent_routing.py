from __future__ import annotations

from typing import Any

from django.db.models import Count, Q

from apps.core.models import Agent, AgentTypeConfig


def list_routable_space_agents(
    space: Any,
    agent_product: str | None = None,
    *,
    active_statuses: tuple[int, ...] = (1,),
) -> list[Agent]:
    queryset = Agent.objects.select_related("agent_type").filter(
        space=space,
        status__in=active_statuses,
        gmt_deleted__isnull=True,
        agent_type__isnull=False,
        agent_type__status=1,
        agent_type__gmt_deleted__isnull=True,
    )

    if agent_product:
        queryset = queryset.filter(
            Q(agent_product=agent_product)
            | Q(agent_type__agent_product=agent_product)
            | Q(agent_type__agent_name=agent_product)
        )
    agents = list(queryset.order_by("id"))
    if agent_product:
        agents = [
            agent
            for agent in agents
            if agent_product
            in (
                agent.agent_product,
                agent.agent_type.agent_product,
                agent.agent_type.agent_name,
            )
        ]
    return agents


def resolve_routable_space_agent(
    space: Any,
    agent_product_or_name: str,
    *,
    active_statuses: tuple[int, ...] = (1,),
) -> Agent | None:
    """Resolve exactly one enabled Agent owned by the requested Space."""
    matching_agents = list_routable_space_agents(
        space,
        agent_product_or_name,
        active_statuses=active_statuses,
    )
    if len(matching_agents) != 1:
        return None
    return matching_agents[0]


def resolve_space_agent_for_query(
    space: Any,
    agent_product_or_name: str | None,
    agent_uuid: Any = None,
    *,
    active_statuses: tuple[int, ...] = (1,),
) -> Agent | None:
    """Resolve one query-scope Agent, including multi-instance products."""

    if agent_uuid:
        agent = Agent.objects.select_related("agent_type").filter(
            uuid=str(agent_uuid),
            space=space,
            status__in=active_statuses,
            gmt_deleted__isnull=True,
            agent_type__isnull=False,
            agent_type__status=1,
            agent_type__gmt_deleted__isnull=True,
        ).first()
        if agent and (
            not agent_product_or_name
            or agent.agent_product == agent_product_or_name
            or agent.agent_type.agent_product == agent_product_or_name
            or agent.agent_type.agent_name == agent_product_or_name
        ):
            return agent
        return None

    if not agent_product_or_name:
        return None
    agents = list_routable_space_agents(
        space,
        agent_product_or_name,
        active_statuses=active_statuses,
    )
    return agents[0] if agents else None


def resolve_space_agent_type(
    space: Any,
    agent_product_or_name: str,
) -> AgentTypeConfig | None:
    """Resolve the runtime config through an Agent owned by the requested Space."""
    agents = list_routable_space_agents(space, agent_product_or_name)
    return agents[0].agent_type if agents else None
