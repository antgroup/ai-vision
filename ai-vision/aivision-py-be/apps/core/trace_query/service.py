from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Mapping

from django.db import transaction
from opentelemetry import trace as otel_trace

from apps.core.metrics import trace_query_count, trace_query_duration
from apps.core.models import Agent
from apps.core.services.agent_routing import (
    list_routable_space_agents,
    resolve_space_agent_for_query,
)
from apps.core.services.observation_subject_service import require_agent_subject
from apps.core.services.personal_diagnosis import sync_personal_diagnosis_after_commit
from apps.core.trace_query.registry import TraceProviderRegistry
from apps.core.trace_query.media_visibility import media_redaction_required, redact_trace_media
from apps.core.trace_query.routing import resolve_trace_source_system
from apps.core.trace_query.semantics.tree_linking import (
    normalize_trace_detail_tree_links,
)
from apps.core.trace_query.types import (
    BaseTraceQuery,
    ObservationDetailQuery,
    SessionDetailQuery,
    SessionIOBatchQuery,
    SessionIOQuery,
    SessionListQuery,
    SessionUsageBatchQuery,
    SpanBatchQuery,
    TraceDetailQuery,
    TraceIOBatchQuery,
    TraceUsageBatchQuery,
    TraceListQuery,
    TraceProvider,
    TraceProviderNotConfiguredError,
)


class TraceQueryService:
    """Service entry point for trace-related queries."""

    def __init__(self, registry: TraceProviderRegistry | None = None) -> None:
        self.registry = registry or TraceProviderRegistry()

    def register_provider(
        self,
        name: str,
        provider: TraceProvider,
        *,
        source_systems: tuple[str, ...] | None = None,
    ) -> None:
        self.registry.register(name, provider, source_systems=source_systems)

    def get_provider(self, name: str) -> TraceProvider:
        return self.registry.get(name)

    @staticmethod
    def _is_truthy_param(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _resolve_provider_name(self, query: BaseTraceQuery) -> str:
        source_system = resolve_trace_source_system(query.space, query.agent_product)
        if source_system:
            provider_name = self.registry.find_name_by_source_system(source_system)
            if provider_name:
                return provider_name
            raise TraceProviderNotConfiguredError(
                f"Unsupported trace source system: {source_system}",
                3011,
            )

        if self.registry.has("langfuse"):
            return "langfuse"

        raise TraceProviderNotConfiguredError(
            "No trace provider configured for this space",
            3011,
        )

    def _resolve_provider(self, query: BaseTraceQuery) -> tuple[str, TraceProvider]:
        name = self._resolve_provider_name(query)
        return name, self.get_provider(name)

    def _resolve_provider_by_source_system(
        self, source_system: str
    ) -> tuple[str, TraceProvider]:
        provider_name = self.registry.find_name_by_source_system(source_system)
        if not provider_name:
            raise TraceProviderNotConfiguredError(
                f"Unsupported trace source system: {source_system}",
                3011,
            )
        return provider_name, self.get_provider(provider_name)

    def _call_provider(
        self,
        query: BaseTraceQuery,
        method: str,
        fn,
        *,
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        # Check the requester before replacing the query's storage subject with
        # the Agent binding. The storage subject does not grant the reader roles.
        redact_media = media_redaction_required(query.space, query.user)
        if redact_media and method == "get_trace_detail":
            query = replace(query, query_params={**dict(query.query_params.items()), "media_view": "1"})
        if source_system_override:
            provider_name, provider = self._resolve_provider_by_source_system(
                source_system_override
            )
        else:
            provider_name, provider = self._resolve_provider(query)
        if getattr(query.space, "pk", None) is not None:
            if query.agent_product:
                agent = resolve_space_agent_for_query(
                    query.space,
                    query.agent_product,
                    agent_uuid=query.agent_uuid,
                    active_statuses=(1, 2),
                )
            else:
                agents = list_routable_space_agents(
                    query.space,
                    active_statuses=(1, 2),
                )
                agent = agents[0] if len(agents) == 1 else None
            if agent is not None:
                binding = agent.observation_subject_binding
                binding.user.observation_subject_id = require_agent_subject(agent)
                query = replace(query, user=binding.user)
        attrs = {"provider": provider_name, "method": method}
        agent_product = query.agent_product
        if agent_product:
            attrs["agent_product"] = agent_product
            span = otel_trace.get_current_span()
            span.set_attribute("aivision.agent_product", agent_product)
        start = time.monotonic()
        try:
            result = fn(provider, query)
            if redact_media:
                result = redact_trace_media(result)
            trace_query_count.add(1, {**attrs, "status": "success"})
            return result
        except Exception:
            trace_query_count.add(1, {**attrs, "status": "error"})
            raise
        finally:
            trace_query_duration.record(time.monotonic() - start, attrs)

    def _promote_pending_agents(
        self,
        space: Any,
        agent_product: str | None,
        result: dict[str, Any],
        agent_uuid: str | None = None,
    ) -> None:
        """后台兜底：Pending Agent 已有 trace 数据时自动升级为已接入。

        修复场景：用户在 onboarding 页面创建 Agent（langfuse 应用、nvidia-nemo-relay
        等）后离开，trace 数据在之后才上报，Agent 的 status 停留在 Pending(2)
        而不会自动升级（前端自动 mark-connected 只在 onboarding 页面生命周期内
        触发一次）。本方法在 trace 列表查询发现对应 agent_product 的真实数据时，
        将该 space 下匹配的 Pending Agent 提升为已接入(1)，与 mark-connected 的
        语义一致。
        """
        if not agent_product:
            return
        if getattr(space, "pk", None) is None:
            return
        meta = result.get("meta") or {}
        try:
            total_items = int(meta.get("totalItems", 0) or 0)
        except (TypeError, ValueError):
            return
        if total_items <= 0:
            return

        agents = list_routable_space_agents(space, agent_product, active_statuses=(1, 2))
        # Product-wide history cannot prove which of several applications sent
        # telemetry. An explicit query identity or a single candidate is needed.
        if not agent_uuid and len(agents) != 1:
            return
        pending_agents = [agent for agent in agents if agent.status == 2
                          and (not agent_uuid or str(agent.uuid) == agent_uuid)]
        for agent in pending_agents:
            with transaction.atomic():
                locked = (
                    Agent.objects.select_for_update()
                    .filter(uuid=agent.uuid, gmt_deleted__isnull=True)
                    .first()
                )
                if locked is None or locked.status != 2:
                    continue
                locked.status = 1
                locked.save(update_fields=["status", "gmt_modified"])
            sync_personal_diagnosis_after_commit(locked)

    def list_traces(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = TraceListQuery(space=space, user=user, query_params=query_params)
        result = self._call_provider(
            query,
            "list_traces",
            lambda p, q: p.query_traces(q) if callable(getattr(type(p), "query_traces", None)) else p.list_traces(q),
            source_system_override=source_system_override,
        )
        # 后台兜底：Pending Agent 已有数据时自动升级为已接入
        self._promote_pending_agents(space, query.agent_product, result, query.agent_uuid)
        return result

    def query_metrics(self, *, space, user, query_params, source_system_override="signoz"):
        query = BaseTraceQuery(space, user, query_params)
        def invoke(provider, scoped_query):
            method = getattr(provider, "query_metrics", None)
            if method is None:
                raise TraceProviderNotConfiguredError("This datasource does not support complete trace metrics", 3011)
            return method(scoped_query)
        return self._call_provider(query, "query_metrics", invoke,
                                   source_system_override=source_system_override)

    def list_facets(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        """多维筛选候选聚合（env/username/scene/tags）。

        provider 不支持聚合时返回空 data（langfuse 走本地聚合）。
        """
        query = TraceListQuery(space=space, user=user, query_params=query_params)
        provider_name, provider = (
            self._resolve_provider_by_source_system(source_system_override)
            if source_system_override
            else self._resolve_provider(query)
        )
        fn = getattr(provider, "list_facets", None)
        if not callable(fn):
            return {"data": {}, "meta": {"supported": False}}
        attrs = {"provider": provider_name, "method": "list_facets"}
        agent_product = query.agent_product
        if agent_product:
            attrs["agent_product"] = agent_product
            span = otel_trace.get_current_span()
            span.set_attribute("aivision.agent_product", agent_product)
        start = time.monotonic()
        try:
            result = fn(query)
            trace_query_count.add(1, {**attrs, "status": "success"})
            return result
        except Exception:
            trace_query_count.add(1, {**attrs, "status": "error"})
            raise
        finally:
            trace_query_duration.record(time.monotonic() - start, attrs)

    def list_mcp_spans(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = TraceListQuery(space=space, user=user, query_params=query_params)
        return self._call_provider(
            query,
            "list_mcp_spans",
            lambda p, q: p.list_mcp_spans(q),
            source_system_override=source_system_override,
        )

    def list_memory_spans(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = TraceListQuery(space=space, user=user, query_params=query_params)
        return self._call_provider(
            query,
            "list_memory_spans",
            lambda p, q: p.list_memory_spans(q),
            source_system_override=source_system_override,
        )

    def list_skill_spans(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        """Storage-side candidate skill spans for the Python skill scan.

        The skill overview re-judges and aggregates the recalled spans in
        Python; providers without the span endpoint (langfuse) explicitly
        report an unsupported capability rather than a verified empty scan.
        """

        def invoke(provider, scoped_query):
            method = getattr(provider, "list_skill_spans", None)
            if method is None:
                return {
                    "supported": False,
                    "reason": "Skill span queries are not supported by this trace data source",
                    "spans": [],
                    "spanMeta": {"totalItems": 0, "totalPages": 0},
                }
            return method(scoped_query)

        query = TraceListQuery(space=space, user=user, query_params=query_params)
        return self._call_provider(
            query,
            "list_skill_spans",
            invoke,
            source_system_override=source_system_override,
        )

    def get_trace_io_batch(
        self,
        *,
        space: Any,
        user: Any,
        trace_ids: list[str],
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = TraceIOBatchQuery(
            space=space,
            user=user,
            query_params=query_params,
            trace_ids=trace_ids,
        )
        return self._call_provider(
            query,
            "get_trace_io_batch",
            lambda p, q: p.get_trace_io_batch(q),
            source_system_override=source_system_override,
        )

    def get_trace_usage_batch(
        self,
        *,
        space: Any,
        user: Any,
        trace_ids: list[str],
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = TraceUsageBatchQuery(
            space=space,
            user=user,
            query_params=query_params,
            trace_ids=trace_ids,
        )
        return self._call_provider(
            query,
            "get_trace_usage_batch",
            lambda p, q: p.get_trace_usage_batch(q),
            source_system_override=source_system_override,
        )

    def get_public_trace_detail(
        self,
        *,
        space: Any,
        user: Any,
        trace_id: str,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        """Public API 专用: 返回 Langfuse 兼容格式 trace detail + OTel 衍生字段。

        仅供 GET /api/public/traces/{trace_id} 调用,
        在 get_trace_detail_langfuse_compat 基础上追加兼容的 OTel 字段
        (environment, sofaTraceId, chatId, scene_code, org_id, uid)。
        """
        query = TraceDetailQuery(
            space=space,
            user=user,
            trace_id=trace_id,
            query_params=query_params,
        )

        def get_public_trace_detail(
            provider: Any,
            detail_query: TraceDetailQuery,
        ) -> dict[str, Any]:
            trace_detail = provider.get_public_trace_detail(detail_query)
            if self._is_truthy_param(detail_query.get_param("langgraph")):
                return normalize_trace_detail_tree_links(trace_detail)
            return trace_detail

        return self._call_provider(
            query,
            "get_public_trace_detail",
            get_public_trace_detail,
            source_system_override=source_system_override,
        )

    def get_trace_detail(
        self,
        *,
        space: Any,
        user: Any,
        trace_id: str,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = TraceDetailQuery(
            space=space,
            user=user,
            trace_id=trace_id,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_trace_detail",
            lambda p, q: normalize_trace_detail_tree_links(p.get_trace_detail(q)),
            source_system_override=source_system_override,
        )

    def get_span_batch(
        self,
        *,
        space: Any,
        user: Any,
        trace_id: str,
        span_ids: list[str],
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = SpanBatchQuery(
            space=space,
            user=user,
            trace_id=trace_id,
            span_ids=span_ids,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_span_batch",
            lambda p, q: p.get_span_batch(q),
            source_system_override=source_system_override,
        )

    def list_sessions(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = SessionListQuery(space=space, user=user, query_params=query_params)
        return self._call_provider(
            query,
            "list_sessions",
            lambda p, q: p.list_sessions(q),
            source_system_override=source_system_override,
        )

    def list_public_sessions(
        self,
        *,
        space: Any,
        user: Any,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        """Public API 专用: 以 Session 为中心聚合返回 Session + 入口 Trace 列表。

        仅供 POST /api/public/sessions/list 调用, 不影响现有 list_sessions() 行为。
        """
        query = SessionListQuery(space=space, user=user, query_params=query_params)
        return self._call_provider(
            query,
            "list_public_sessions",
            lambda p, q: p.list_public_sessions(q),
            source_system_override=source_system_override,
        )

    def get_session_io_batch(
        self,
        *,
        space: Any,
        user: Any,
        session_ids: list[str],
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = SessionIOBatchQuery(
            space=space,
            user=user,
            session_ids=session_ids,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_session_io_batch",
            lambda p, q: p.get_session_io_batch(q),
            source_system_override=source_system_override,
        )

    def get_session_io(
        self,
        *,
        space: Any,
        user: Any,
        session_id: str,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = SessionIOQuery(
            space=space,
            user=user,
            session_id=session_id,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_session_io",
            lambda p, q: p.get_session_io(q),
            source_system_override=source_system_override,
        )

    def get_session_usage_batch(
        self,
        *,
        space: Any,
        user: Any,
        session_ids: list[str],
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = SessionUsageBatchQuery(
            space=space,
            user=user,
            session_ids=session_ids,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_session_usage_batch",
            lambda p, q: p.get_session_usage_batch(q),
            source_system_override=source_system_override,
        )

    def get_session_detail(
        self,
        *,
        space: Any,
        user: Any,
        session_id: str,
        query_params: Mapping[str, Any],
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = SessionDetailQuery(
            space=space,
            user=user,
            session_id=session_id,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_session_detail",
            lambda p, q: p.get_session_detail(q),
            source_system_override=source_system_override,
        )

    def get_observation_detail(
        self,
        *,
        space: Any,
        user: Any,
        observation_id: str,
        query_params: Mapping[str, Any],
        trace_id: str | None = None,
        source_system_override: str | None = None,
    ) -> dict[str, Any]:
        query = ObservationDetailQuery(
            space=space,
            user=user,
            observation_id=observation_id,
            trace_id=trace_id,
            query_params=query_params,
        )
        return self._call_provider(
            query,
            "get_observation_detail",
            lambda p, q: p.get_observation_detail(q),
            source_system_override=source_system_override,
        )
