"""Build a scoped SigNoz diagnosis dataset. Langfuse remains an observation source."""

from __future__ import annotations

import logging
from typing import Any

from apps.core.services.agent_routing import resolve_routable_space_agent
from apps.core.services.observation_subject_service import (
    require_agent_subject,
    require_default_subject,
)
from apps.core.trace_query.config import resolve_configured_agent_product
from apps.core.trace_query.routing import get_trace_datasource

logger = logging.getLogger(__name__)

CODING_RUNTIME_TYPES = frozenset(
    {"cc", "codex", "antcodex", "claudecode", "antcc", "codefuse", "dsh"}
)

SIGNOZ_SOURCE_SYSTEM = "signoz"
# SigNoz OB 物理表与 Collector 提升列（见 signoz-otel-collector-ob exporter schema）
SIGNOZ_DEFAULT_TABLE = "signoz_traces"
SIGNOZ_SPACE_COLUMN = "space_id"
SIGNOZ_USERNAME_COLUMN = "user_id"
SIGNOZ_AGENT_PRODUCT_COLUMN = "agent_product"
SIGNOZ_CODING_ROOT_SPAN_WHERE = "`parent_span_id` = ''"


class UnsupportedDataSourceError(Exception):
    """AgentLens runner cannot consume this DataSource type."""


def build_default_dataset(
    space: Any,
    user: Any,
    agent_product: str,
    *,
    agent: Any = None,
) -> dict[str, Any] | None:
    """Build a complete default dataset payload {type, spec} for AgentLens.

    Returns None if no DataSource is configured.
    Raises UnsupportedDataSourceError for langfuse or other unsupported sources.
    """
    ds = get_trace_datasource(
        space,
        agent_product=agent_product,
        agent_uuid=getattr(agent, "uuid", None),
        source_system="signoz",
    )
    if ds is None:
        langfuse = get_trace_datasource(
            space,
            agent_product=agent_product,
            agent_uuid=getattr(agent, "uuid", None),
            source_system="langfuse",
        )
        if langfuse is not None:
            raise UnsupportedDataSourceError(
                "诊断需要配置 SigNoz 数据源；Langfuse 当前仅支持观测查询"
            )
        return None

    source_system = ds.source_system or ""
    config = ds.config or {}
    at = ds.agent_type
    is_personal = getattr(space, "type", None) != 2

    is_signoz = source_system == SIGNOZ_SOURCE_SYSTEM
    if not is_signoz:
        if source_system == "langfuse":
            raise UnsupportedDataSourceError(
                "诊断需要配置 SigNoz 数据源；Langfuse 当前仅支持观测查询"
            )
        raise UnsupportedDataSourceError(f"AgentLens 暂不支持 {source_system} 数据源")

    table = str(config.get("table_name") or SIGNOZ_DEFAULT_TABLE)
    username_column = str(config.get("username_column") or SIGNOZ_USERNAME_COLUMN)
    agent_product_column = str(
        config.get("agent_product_column") or SIGNOZ_AGENT_PRODUCT_COLUMN
    )
    space_filter = str(getattr(space, "uuid", None) or "")

    runtime_type = at.runtime_type if at else None
    product_filter = resolve_configured_agent_product(config)

    # scope_type: personal=个人空间(按用户过滤), space=业务空间(不按用户过滤)
    scope_type = "personal" if is_personal else "space"
    username = ""
    if is_personal and user is not None:
        # SigNoz diagnosis must use the same Agent-bound identity as ingestion
        # and trace queries; a separate SigNoz user binding is not required.
        agent = agent or (
            resolve_routable_space_agent(space, agent_product, active_statuses=(1, 2))
            if is_signoz and getattr(space, "pk", None) and agent_product
            else None
        )
        username = (
            require_agent_subject(agent)
            if agent is not None
            else require_default_subject(
                space=space,
                user=user,
                source_system=source_system,
            )
        )

    # 个人空间 Coding 只选择每条 trace 的根 span。
    # 后续按 trace_id 回补完整 span，因此根筛选不会丢失诊断材料。
    where = ""
    if is_personal and str(runtime_type or "").lower() in CODING_RUNTIME_TYPES:
        where = SIGNOZ_CODING_ROOT_SPAN_WHERE

    spec: dict[str, Any] = {
        # 1. 数据源字段
        # 2. 系统过滤字段
        "scope_type": scope_type,
        # 3. 查询参数
        "query": {
            "table": table,
            "where": where,
            "time_range": "2m",
            "time_offset": "1h",
            "limit": 100,
            "group_time_window_min": 10,
            "group_batch_size": 5,
        },
        # 4. 诊断执行字段
        "runtime_type": runtime_type,
    }
    if space_filter:
        spec["space_column"] = SIGNOZ_SPACE_COLUMN
        spec["space_filter"] = space_filter
    if is_personal:
        spec["username_column"] = username_column
        spec["username"] = username
    if product_filter:
        spec["agent_product_column"] = agent_product_column
        spec["agent_product_filter"] = product_filter
    return {"type": "signoz", "spec": spec}
