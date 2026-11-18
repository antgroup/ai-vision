"""Dashboard 数据统计视图"""

from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from rest_framework.views import APIView

from ..models import Agent, AgentTypeConfig, Session, Space
from .common import check_space_access_or_error, error_response, success_response


def get_time_range_filter(time_range):
    now = timezone.now()
    if time_range == "24h":
        return now - timedelta(hours=24)
    elif time_range == "3d":
        return now - timedelta(days=3)
    elif time_range == "7d":
        return now - timedelta(days=7)
    elif time_range == "30d":
        return now - timedelta(days=30)
    return now - timedelta(hours=24)


class DashboardSummaryView(APIView):
    def get(self, request):
        user = request.user

        spaces = Space.objects.filter(members__user=user, status=1)
        total_agents = Agent.objects.filter(space__in=spaces, status=1).count()
        total_sessions = Session.objects.filter(agent__space__in=spaces).count()
        total_tokens = Session.objects.filter(agent__space__in=spaces).aggregate(
            input_sum=Sum("tokens_input"), output_sum=Sum("tokens_output")
        )

        return success_response(
            {
                "total_spaces": spaces.count(),
                "total_agents": total_agents,
                "total_sessions": total_sessions,
                "total_tokens_input": total_tokens["input_sum"] or 0,
                "total_tokens_output": total_tokens["output_sum"] or 0,
            }
        )


class DashboardMetricsView(APIView):
    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        time_range = request.query_params.get("timeRange", "24h")
        start_time = get_time_range_filter(time_range)

        agents = Agent.objects.filter(space__uuid=space_id, status=1)
        agent_ids = list(agents.values_list("id", flat=True))

        sessions = Session.objects.filter(
            agent_id__in=agent_ids,
            gmt_create__gte=start_time,
        )

        total_tokens = sessions.aggregate(
            input_sum=Sum("tokens_input"),
            output_sum=Sum("tokens_output"),
        )
        total_calls = sessions.count()
        success_calls = sessions.filter(status=1).count()
        success_rate = (
            round((success_calls / total_calls * 100), 2) if total_calls > 0 else 0
        )

        return success_response(
            {
                "agent_count": agents.count(),
                "total_calls": total_calls,
                "success_rate": success_rate,
                "total_tokens": (total_tokens["input_sum"] or 0)
                + (total_tokens["output_sum"] or 0),
                "tokens_input": total_tokens["input_sum"] or 0,
                "tokens_output": total_tokens["output_sum"] or 0,
            }
        )


class TopAgentsView(APIView):
    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        limit = int(request.query_params.get("limit", 10))
        time_range = request.query_params.get("timeRange", "24h")
        start_time = get_time_range_filter(time_range)

        agents = Agent.objects.filter(space__uuid=space_id, status=1).select_related()

        result = []
        for agent in agents:
            sessions = Session.objects.filter(
                agent=agent,
                gmt_create__gte=start_time,
            )
            total_tokens = sessions.aggregate(
                input_sum=Sum("tokens_input"),
                output_sum=Sum("tokens_output"),
            )
            tokens = (total_tokens["input_sum"] or 0) + (
                total_tokens["output_sum"] or 0
            )
            total_calls = sessions.count()
            success_calls = sessions.filter(status=1).count()
            success_rate = (
                round((success_calls / total_calls * 100), 2) if total_calls > 0 else 0
            )

            result.append(
                {
                    "id": agent.id,
                    "name": agent.name,
                    "custom_name": agent.custom_name,
                    "agent_product": agent.agent_product,
                    "tokens": tokens,
                    "calls": total_calls,
                    "success_rate": success_rate,
                }
            )

        result.sort(key=lambda x: x["tokens"], reverse=True)
        return success_response(result[:limit])


class RecentAnomaliesView(APIView):
    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        limit = int(request.query_params.get("limit", 5))
        # V1-SEC-05: add optional time filter (default 7d)
        time_range = request.query_params.get("timeRange", "7d")
        start_time = get_time_range_filter(time_range)

        agents = Agent.objects.filter(space__uuid=space_id, status=1)
        agent_ids = list(agents.values_list("id", flat=True))
        agent_map = {a.id: a for a in agents}

        sessions = Session.objects.filter(
            agent_id__in=agent_ids,
            status=2,
            gmt_create__gte=start_time,
        ).order_by("-gmt_create")[:limit]

        result = []
        for session in sessions:
            agent = agent_map.get(session.agent_id)
            result.append(
                {
                    "trace_id": session.trace_id,
                    "agent_name": agent.custom_name or agent.name if agent else None,
                    "error_type": session.error_type,
                    "error_message": session.error_message,
                    "gmt_create": session.gmt_create.isoformat()
                    if session.gmt_create
                    else None,
                }
            )

        return success_response(result)


class TokenDistributionView(APIView):
    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        time_range = request.query_params.get("timeRange", "24h")
        start_time = get_time_range_filter(time_range)

        agents = Agent.objects.filter(space__uuid=space_id, status=1)
        agent_type_configs = {
            atc.agent_name: atc.agent_product
            for atc in AgentTypeConfig.objects.shared_runtime()
        }

        agent_ids = list(agents.values_list("id", flat=True))
        sessions = Session.objects.filter(
            agent_id__in=agent_ids,
            gmt_create__gte=start_time,
        ).select_related("agent", "agent__agent_type")

        type_tokens = {}
        for session in sessions:
            agent = session.agent
            agent_name = agent.agent_product if agent else "unknown"
            agent_product = agent_type_configs.get(agent_name, agent_name)
            tokens = (session.tokens_input or 0) + (session.tokens_output or 0)
            identity = (agent_name, agent_product)
            if identity not in type_tokens:
                type_tokens[identity] = 0
            type_tokens[identity] += tokens

        total_tokens = sum(type_tokens.values())
        result = []
        for (agent_name, agent_product), tokens in type_tokens.items():
            percentage = (
                round((tokens / total_tokens * 100), 2) if total_tokens > 0 else 0
            )
            result.append(
                {
                    "agent_name": agent_name,
                    "agent_product": agent_product,
                    "tokens": tokens,
                    "percentage": percentage,
                }
            )

        result.sort(key=lambda x: x["tokens"], reverse=True)
        return success_response(result)


class RecentSessionsView(APIView):
    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        limit = int(request.query_params.get("limit", 10))
        # V1-SEC-05: add optional time filter (default 24h)
        time_range = request.query_params.get("timeRange", "24h")
        start_time = get_time_range_filter(time_range)

        agents = Agent.objects.filter(space__uuid=space_id, status=1)
        agent_ids = list(agents.values_list("id", flat=True))
        agent_map = {a.id: a for a in agents}

        sessions = Session.objects.filter(
            agent_id__in=agent_ids,
            gmt_create__gte=start_time,
        ).order_by("-gmt_create")[:limit]

        result = []
        for session in sessions:
            agent = agent_map.get(session.agent_id)
            result.append(
                {
                    "trace_id": session.trace_id,
                    "agent_name": agent.custom_name or agent.name if agent else None,
                    "status": session.status,
                    "tokens_input": session.tokens_input,
                    "tokens_output": session.tokens_output,
                    "gmt_create": session.gmt_create.isoformat()
                    if session.gmt_create
                    else None,
                }
            )

        return success_response(result)
