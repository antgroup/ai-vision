from __future__ import annotations

from typing import Any

from apps.core.services.agent_routing import (
    list_routable_space_agents,
    resolve_routable_space_agent,
    resolve_space_agent_for_query,
)
from apps.core.trace_query.types import (
    BaseTraceQuery,
    TraceProviderNotConfiguredError,
    TraceQueryServiceError,
)

TRACE_QUERY_ACTIVE_AGENT_STATUSES = (1, 2)

# Product-less callers first use the single configured default selected by
# ``get_trace_datasource``.  This order is only consulted when that lookup is
# ambiguous (for example, multiple agents expose different trace backends).
# Prefer SigNoz when no single default can be selected across Agents.
TRACE_SOURCE_SYSTEM_FALLBACK_ORDER = (
    "signoz",
    "langfuse",
)


def list_space_agent_products(
    space: Any, agent_product: str | None = None
) -> list[str]:
    if agent_product:
        agent = resolve_routable_space_agent(
            space,
            agent_product,
            active_statuses=TRACE_QUERY_ACTIVE_AGENT_STATUSES,
        )
        return [agent.agent_type.agent_product] if agent else []

    agents = list_routable_space_agents(
        space,
        active_statuses=TRACE_QUERY_ACTIVE_AGENT_STATUSES,
    )
    products = {
        agent.agent_type.agent_product if agent.agent_type else agent.agent_product
        for agent in agents
    }
    return sorted(products)


def get_space_observation_project(space: Any) -> Any | None:
    from apps.core.models import SpaceObservationProject

    return (
        SpaceObservationProject.objects.select_related("endpoint_config")
        .filter(
            space=space,
            status=1,
            gmt_deleted__isnull=True,
        )
        .order_by("id")
        .first()
    )


def get_space_datasource_binding(
    space: Any, datasource_type: str = "traces"
) -> Any | None:
    from apps.core.models import SpaceDatasourceBinding

    project = get_space_observation_project(space)
    if not project:
        return None

    bindings = list(
        SpaceDatasourceBinding.objects.filter(
            space_observation_project=project,
            datasource_type=datasource_type,
            enabled=1,
            gmt_deleted__isnull=True,
        ).order_by("-is_default", "id")
    )
    return _select_route_record(
        bindings,
        error_message=(
            f"Multiple {datasource_type} backends configured for this space; "
            "please set exactly one default route"
        ),
    )


def _select_route_record(records: list[Any], *, error_message: str) -> Any | None:
    if not records:
        return None

    default_records = [
        record for record in records if getattr(record, "is_default", 0) == 1
    ]
    candidates = default_records or records
    if len(candidates) != 1:
        raise TraceProviderNotConfiguredError(error_message, 3011)
    return candidates[0]


def _first_space_signoz_trace_route(space: Any) -> Any | None:
    """Return any SigNoz trace route of the space without uniqueness checks.

    Market registration creates one SigNoz datasource per Agent (each carrying
    its own ``agent_product_filter``). Space-wide queries cannot select a
    single default among them, but the shared backend is still routable.
    """
    from apps.core.models import DataSource

    agents = list_routable_space_agents(
        space, active_statuses=TRACE_QUERY_ACTIVE_AGENT_STATUSES
    )
    agent_type_ids = [
        agent.agent_type_id for agent in agents if agent.agent_type_id is not None
    ]
    if not agent_type_ids:
        return None
    return (
        DataSource.objects.filter(
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            gmt_deleted__isnull=True,
            agent_type_id__in=agent_type_ids,
            agent_type__status=1,
            agent_type__gmt_deleted__isnull=True,
        )
        .order_by("-is_default", "id")
        .first()
    )


def get_trace_datasource(
    space: Any,
    *,
    agent_product: str | None = None,
    agent_uuid: Any = None,
    source_system: str | None = None,
) -> Any | None:
    from apps.core.models import DataSource

    if agent_product:
        # 多实例产品（如 langfuse）在同一 space 下可能有多个 active Agent，
        # 仅按 agent_product 无法唯一路由；优先用 agent_uuid 精确匹配，
        # 否则回退到恰好一个 active Agent 的旧行为。
        agent = resolve_space_agent_for_query(
            space,
            agent_product,
            agent_uuid=agent_uuid,
            active_statuses=TRACE_QUERY_ACTIVE_AGENT_STATUSES,
        )
        if not agent:
            raise TraceProviderNotConfiguredError(
                "Agent query route not found",
                3011,
            )
        agents = [agent]
    else:
        agents = list_routable_space_agents(
            space,
            active_statuses=TRACE_QUERY_ACTIVE_AGENT_STATUSES,
        )
        if not agents:
            return None
    agent_type_ids = list(
        {agent.agent_type_id for agent in agents if agent.agent_type_id is not None}
    )
    queryset = DataSource.objects.select_related("agent_type").filter(
        datasource_type="traces",
        enabled=1,
        gmt_deleted__isnull=True,
        agent_type_id__in=agent_type_ids,
        agent_type__status=1,
        agent_type__gmt_deleted__isnull=True,
        agent_type__agents__id__in=[agent.id for agent in agents],
        agent_type__agents__space=space,
        agent_type__agents__status__in=TRACE_QUERY_ACTIVE_AGENT_STATUSES,
        agent_type__agents__gmt_deleted__isnull=True,
    )
    eligible_queryset = queryset
    if source_system:
        queryset = queryset.filter(source_system=source_system)
    datasource = _select_route_record(
        list(queryset.order_by("-is_default", "id").distinct()),
        error_message=(
            "Multiple trace backends configured for this space; "
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


def get_personal_trace_datasource(
    space: Any,
    *,
    agent_product: str | None = None,
    source_system: str | None = None,
) -> Any | None:
    return get_trace_datasource(
        space,
        agent_product=agent_product,
        source_system=source_system,
    )


def resolve_trace_source_system(
    space: Any, agent_product: str | None = None
) -> str | None:
    if agent_product:
        datasource = get_trace_datasource(space, agent_product=agent_product)
        return datasource.source_system if datasource else None
    else:
        try:
            datasource = get_trace_datasource(space)
        except TraceProviderNotConfiguredError:
            datasource = None
            for source_system in TRACE_SOURCE_SYSTEM_FALLBACK_ORDER:
                try:
                    datasource = get_trace_datasource(
                        space,
                        source_system=source_system,
                    )
                except TraceProviderNotConfiguredError:
                    # Per-Agent SigNoz routes sharing one backend stay usable
                    # for space-wide queries even without a single default.
                    if source_system == "signoz" and _first_space_signoz_trace_route(
                        space
                    ):
                        return "signoz"
                    continue
                if datasource:
                    break
    if datasource:
        return datasource.source_system

    if getattr(space, "type", None) == 2:
        binding = get_space_datasource_binding(space, datasource_type="traces")
        if binding and getattr(binding, "source_system", None):
            return binding.source_system
        return "langfuse"

    return None


def resolve_langfuse_runtime_config(
    space: Any,
    *,
    agent_product: str | None = None,
) -> dict[str, Any]:
    from apps.core.views import get_space_observation_runtime_config

    config = get_space_observation_runtime_config(space, agent_product=agent_product)
    if not config:
        raise TraceProviderNotConfiguredError(
            "Langfuse not configured for this space",
            3011,
        )
    return config


def resolve_signoz_runtime_config(query: BaseTraceQuery) -> dict[str, Any]:
    try:
        datasource = get_trace_datasource(
            query.space,
            agent_product=query.agent_product,
            agent_uuid=query.agent_uuid,
            source_system="signoz",
        )
    except TraceProviderNotConfiguredError:
        if query.agent_product:
            raise
        datasource = _first_space_signoz_trace_route(query.space)
    if datasource:
        config = dict(datasource.config or {})
        if not query.agent_product:
            # Space-wide views must not inherit one Agent's product filter.
            config.pop("agent_product_filter", None)
            config.pop("agent_product", None)
        return config
    raise TraceProviderNotConfiguredError(
        "SigNoz not configured for this Agent in this space", 3011
    )
