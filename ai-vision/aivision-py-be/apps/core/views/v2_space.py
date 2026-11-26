"""V2 Space-scoped API views (agents listing, etc.)."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..cache import cached_query
from ..models import Agent, AgentTypeConfig, Space
from ..services.plugin_artifacts import market_display_scope
from .common import check_space_access_or_error, error_response, success_response


def _render_cli_command(command, agent_uuid):
    """Bind a catalog CLI command to the Agent provisioned in this space."""
    if not command or not agent_uuid:
        return command
    if "{agent_uuid}" in command:
        return command.replace("{agent_uuid}", agent_uuid)
    # Compatibility for Codex rows seeded before agent-scoped ingest tokens.
    if "--agent-uuid" not in command and " plugin install " in command:
        return command.replace(
            " plugin install ",
            f" --agent-uuid {agent_uuid} plugin install ",
            1,
        )
    return command


def _agent_type_configs_for_space(space):
    """Return AgentTypeConfig rows exposed in the catalog for the given space.

    Platform spaces (platform_id set) can only host platform-owned AgentTypes per
    the platform control plane constraint, so the catalog only includes
    platform-owned types. Personal / shared runtime spaces can't bind platform
    types, so the catalog mirrors shared_runtime (installable types).
    """
    return cached_query(
        "agent_types:shared_runtime",
        lambda: list(AgentTypeConfig.objects.shared_runtime()),
    )


class V2SpaceAgentsView(APIView):
    """
    GET /api/v2/spaces/{space_id}/agents

    Returns agents for a space with merged AgentTypeConfig metadata.
    Single request replaces the V1 pattern of parallel /agents + /agent-types.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        agents = list(
            Agent.objects.select_related("agent_type")
            .filter(space__uuid=space_id, status__in=[1, 2])
            .order_by("-gmt_create")
        )

        all_configs = _agent_type_configs_for_space(space)
        config_by_id = {atc.id: atc for atc in all_configs}
        config_by_name = {atc.agent_name: atc for atc in all_configs}
        config_by_product = {atc.agent_product: atc for atc in all_configs}
        agent_uuid_by_config_id = {}

        agent_list = []
        for agent in agents:
            config = (
                agent.agent_type
                or config_by_id.get(agent.agent_type_id)
                or config_by_product.get(agent.agent_product)
                or config_by_name.get(agent.agent_product)
            )
            if config:
                agent_uuid_by_config_id.setdefault(config.id, agent.uuid)
            agent_list.append(
                {
                    "id": agent.id,
                    "uuid": agent.uuid,
                    "agent_name": config.agent_name if config else agent.agent_product,
                    "agent_product": (
                        config.agent_product if config else agent.agent_product
                    ),
                    "agent_type_id": agent.agent_type_id,
                    "runtime_type": config.runtime_type if config else "",
                    "display_name": config.display_name if config else None,
                    "display_scope": config.display_scope if config else None,
                    "name": agent.name,
                    "custom_name": agent.custom_name,
                    "metadata": agent.metadata,
                    "category": config.category if config else None,
                    "icon": config.icon if config else None,
                    "description": config.description if config else None,
                    "status": agent.status,
                    "last_active_at": (
                        agent.last_active_at.isoformat()
                        if agent.last_active_at
                        else None
                    ),
                }
            )

        agent_types_list = [
            {
                "agent_name": atc.agent_name,
                "agent_product": atc.agent_product,
                "runtime_type": atc.runtime_type,
                "display_name": atc.display_name,
                "display_scope": market_display_scope(
                    atc.display_scope, atc.config_guide
                ),
                "category": atc.category,
                "description": atc.description,
                "icon": atc.icon,
                "setup_mode": atc.setup_mode,
                "cli_command": _render_cli_command(
                    atc.cli_command, agent_uuid_by_config_id.get(atc.id)
                ),
                "config_guide": atc.config_guide,
                "capabilities": atc.capabilities,
                "sort_order": atc.sort_order,
            }
            for atc in all_configs
            if atc.status == 1
        ]

        return success_response(
            {
                "agents": agent_list,
                "agent_types": agent_types_list,
                "disabled_agents": list(
                    Agent.objects.filter(
                        space__uuid=space_id, status=0, gmt_deleted__isnull=True
                    ).values("uuid", "agent_product")
                ),
            }
        )
