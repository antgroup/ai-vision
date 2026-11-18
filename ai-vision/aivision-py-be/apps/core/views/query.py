import logging
from datetime import UTC, timedelta

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..adapters import QueryAdapterFactory, QueryContext
from ..models import (
    AgentDashboardPanel,
    AgentTypeConfig,
    DataSource,
    Space,
)
from ..services.observation_subject_service import require_agent_subject
from ..services.agent_routing import resolve_routable_space_agent
from .common import check_space_access_or_error, error_response, success_response

logger = logging.getLogger(__name__)

MAX_TIME_RANGE_DAYS = 90


def find_datasource_for_item(agent_type_config, data_item_key):
    """
    遍历 AgentType 关联的所有 DataSource，
    找到 config.queries 中包含 data_item_key 的那个 DataSource。
    """
    from ..cache import cached_query

    datasources = cached_query(
        f"datasources:agent_type:{agent_type_config.id}",
        lambda: list(
            DataSource.objects.filter(
                agent_type=agent_type_config,
                enabled=1,
                gmt_deleted__isnull=True,
            )
        ),
    )
    for datasource in datasources:
        config = datasource.config or {}
        queries = config.get("queries", {})
        if data_item_key in queries:
            return datasource
    return None


def parse_time_range(time_range):
    """将 time_range 字符串（如 '24h', '7d'）转换为 (time_start, time_end) ISO 字符串"""
    now = timezone.now()

    range_mapping = {
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "3h": timedelta(hours=3),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "24h": timedelta(hours=24),
        "2d": timedelta(days=2),
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
        "14d": timedelta(days=14),
        "30d": timedelta(days=30),
        "2days": timedelta(days=2),
    }

    delta = range_mapping.get(time_range, timedelta(hours=24))
    time_start = (now - delta).isoformat()
    time_end = now.isoformat()
    return time_start, time_end


def resolve_time_window(request):
    from_ts = request.query_params.get("fromTimestamp")
    to_ts = request.query_params.get("toTimestamp")

    if from_ts and to_ts:
        time_start = parse_datetime(from_ts)
        time_end = parse_datetime(to_ts)

        if not time_start or not time_end:
            raise ValueError("Invalid fromTimestamp or toTimestamp")

        if time_start > time_end:
            raise ValueError("fromTimestamp cannot be later than toTimestamp")

        if timezone.is_naive(time_start):
            time_start = timezone.make_aware(time_start, UTC)

        if timezone.is_naive(time_end):
            time_end = timezone.make_aware(time_end, UTC)

        # V1-SEC-03: enforce max time range span to prevent unbounded queries
        span_days = (time_end - time_start).days
        if span_days > MAX_TIME_RANGE_DAYS:
            raise ValueError(
                f"Time range cannot exceed {MAX_TIME_RANGE_DAYS} days (requested {span_days} days)"
            )

        return from_ts, to_ts, "custom"

    time_range = request.query_params.get("time_range", "24h")
    time_start, time_end = parse_time_range(time_range)
    return time_start, time_end, time_range


class DataQueryView(APIView):
    """
    通用数据查询接口。

    GET /api/v1/spaces/{space_id}/query/
        ?agent_product=claude_code
        &data_item=session_count
        &time_range=24h

    V1-SEC-01: 已添加 space 权限校验。
    V1-SEC-02: 不接受客户端用户身份参数。用户身份始终从 request.user 获取。
    V1-SEC-03: 已添加时间范围上限校验（最大 90 天）。
    V1-SEC-06: 已对错误信息脱敏。
    """

    def get(self, request, space_id):
        user = request.user

        # V1-SEC-01: 校验 space 权限
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(user, space)
        if access_error:
            return access_error

        # V1-SEC-02: 用户身份只从 request.user 获取

        agent_product = request.query_params.get("agent_product")
        data_item_key = request.query_params.get("data_item")
        if not agent_product or not data_item_key:
            return error_response(
                "Missing required parameters: agent_product, data_item"
            )

        agent = resolve_routable_space_agent(space, agent_product)
        if not agent:
            return error_response(f"Agent type not found: {agent_product}", code=3001)
        agent_type_config = agent.agent_type

        datasource = find_datasource_for_item(agent_type_config, data_item_key)
        if not datasource:
            return error_response(
                f"No datasource found for data item: {data_item_key}", code=3002
            )

        username = require_agent_subject(agent)

        adapter = QueryAdapterFactory.create(datasource)

        try:
            time_start, time_end, time_range = resolve_time_window(request)
        except ValueError as exc:
            return error_response(str(exc))

        context = QueryContext(
            user_id=user.id,
            username=username,
            agent_product=agent_product,
            agent_instance=None,
            data_item_key=data_item_key,
            time_start=time_start,
            time_end=time_end,
            time_range=time_range,
            space_id=space_id,
            limit=int(request.query_params.get("limit", 100)),
            offset=int(request.query_params.get("offset", 0)),
        )

        result = adapter.query(context)

        if result.success:
            return success_response(
                {
                    "value_type": result.value_type.value,
                    "data": result.data,
                    "execution_time_ms": result.execution_time_ms,
                }
            )

        # V1-SEC-06: 对错误信息脱敏，不暴露数据源细节
        logger.error(
            "DataQueryView query failed for agent_product=%s data_item=%s: %s",
            agent_product,
            data_item_key,
            result.error_message,
        )
        return error_response("Query execution failed", code=3003)


class AgentDashboardPanelListView(APIView):
    """
    获取某个 AgentType 的 Dashboard 面板配置。

    GET /api/v1/agent-types/{agent_name}/panels/
        ?page=overview

    V1-SEC-04: 已显式添加 IsAuthenticated 权限校验。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, agent_name):
        from urllib.parse import unquote

        page = request.query_params.get("page", "overview")
        agent_name_decoded = unquote(agent_name)

        from ..cache import cached_query

        agent_type_config = cached_query(
            f"agent_type:name:{agent_name_decoded}",
            lambda: AgentTypeConfig.objects.active_shared_runtime()
            .filter(
                models.Q(agent_name=agent_name_decoded)
                | models.Q(agent_product=agent_name_decoded)
            )
            .first(),
        )

        if not agent_type_config:
            return success_response({"groups": []})

        panels = cached_query(
            f"panels:agent_type:{agent_type_config.id}:page:{page}",
            lambda: list(
                AgentDashboardPanel.objects.filter(
                    agent_type=agent_type_config,
                    page=page,
                    status=1,
                    gmt_deleted__isnull=True,
                ).order_by("sort_order")
            ),
        )

        groups: dict[str, list] = {}
        group_min_sort: dict[str, int] = {}
        for panel in panels:
            group_name = panel.group_name
            if group_name not in groups:
                groups[group_name] = []
                group_min_sort[group_name] = panel.sort_order
            groups[group_name].append(
                {
                    "id": panel.id,
                    "panel_type": panel.panel_type,
                    "title": panel.title,
                    "description": panel.description,
                    "data_item_key": panel.data_item_key,
                    "datasource_type": panel.datasource_type,
                    "sort_order": panel.sort_order,
                    "grid_width": panel.grid_width,
                    "group_name": group_name,
                    "panel_config": panel.panel_config,
                }
            )

        result = [
            {"group_name": name, "panels": panels_list}
            for name, panels_list in sorted(
                groups.items(), key=lambda x: group_min_sort[x[0]]
            )
        ]

        return success_response({"groups": result})


class SpaceDashboardPanelListView(APIView):
    """
    获取 Space 级别全局 Dashboard 的面板配置。

    GET /api/v1/spaces/{space_id}/dashboard/panels
        ?page=overview
    """

    def get(self, request, space_id):
        from ..models import Agent, Space
        from .common import check_space_access_or_error, error_response

        page = request.query_params.get("page", "overview")

        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        agent_products = list(
            Agent.objects.filter(space__uuid=space_id, status=1)
            .values_list("agent_product", flat=True)
            .distinct()
        )

        if page == "overview":
            panels_config = {
                "groups": [
                    {
                        "group_name": "核心指标",
                        "panels": [
                            {
                                "id": 1,
                                "panel_type": "stat_card",
                                "title": "总调用次数",
                                "description": None,
                                "data_item_key": "total_calls",
                                "datasource_type": "metrics",
                                "sort_order": 1,
                                "grid_width": 3,
                                "panel_config": {"color_scheme": "blue"},
                            },
                            {
                                "id": 2,
                                "panel_type": "stat_card",
                                "title": "成功率",
                                "description": None,
                                "data_item_key": "success_rate",
                                "datasource_type": "metrics",
                                "sort_order": 2,
                                "grid_width": 3,
                                "panel_config": {
                                    "suffix": "%",
                                    "decimal_places": 1,
                                    "color_scheme": "green",
                                },
                            },
                            {
                                "id": 3,
                                "panel_type": "stat_card",
                                "title": "Agent 数量",
                                "description": None,
                                "data_item_key": "agent_count",
                                "datasource_type": "metrics",
                                "sort_order": 3,
                                "grid_width": 3,
                                "panel_config": {"color_scheme": "purple"},
                            },
                            {
                                "id": 4,
                                "panel_type": "stat_card",
                                "title": "Token 使用量",
                                "description": None,
                                "data_item_key": "total_tokens",
                                "datasource_type": "metrics",
                                "sort_order": 4,
                                "grid_width": 3,
                                "panel_config": {"color_scheme": "blue"},
                            },
                        ],
                    },
                    {
                        "group_name": "监控列表",
                        "panels": [
                            {
                                "id": 5,
                                "panel_type": "recent_anomalies",
                                "title": "最近异常",
                                "description": None,
                                "data_item_key": "recent_anomalies",
                                "datasource_type": "metrics",
                                "sort_order": 5,
                                "grid_width": 6,
                                "panel_config": {"limit": 5},
                            },
                            {
                                "id": 6,
                                "panel_type": "recent_sessions",
                                "title": "最近会话",
                                "description": None,
                                "data_item_key": "recent_sessions",
                                "datasource_type": "metrics",
                                "sort_order": 6,
                                "grid_width": 6,
                                "panel_config": {"limit": 5},
                            },
                        ],
                    },
                    {
                        "group_name": "统计排名",
                        "panels": [
                            {
                                "id": 7,
                                "panel_type": "token_ranking",
                                "title": "Agent Token 排名",
                                "description": None,
                                "data_item_key": "token_ranking",
                                "datasource_type": "metrics",
                                "sort_order": 7,
                                "grid_width": 4,
                                "panel_config": {"limit": 5},
                            },
                            {
                                "id": 8,
                                "panel_type": "table",
                                "title": "Agent 概览",
                                "description": None,
                                "data_item_key": "agent_overview",
                                "datasource_type": "metrics",
                                "sort_order": 8,
                                "grid_width": 8,
                                "panel_config": {"page_size": 10},
                            },
                        ],
                    },
                ],
                "agent_products": agent_products,
            }
            return success_response(panels_config)

        return success_response({"groups": [], "agent_products": agent_products})
