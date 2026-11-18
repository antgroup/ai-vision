"""V2 Agent Dashboard Query API views.

对应设计文档 docs/v2/backend-v2-apis.md。
"""

import logging
import re
import time

import requests
from django.conf import settings as django_settings
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..cache import cached_query
from ..crypto.agent_query_crypto import (
    decrypt_agent_query_payload,
    is_encryption_enabled,
    is_plain_allowed,
)
from ..models import AgentTypeConfig, DataSource, Space
from ..query.errors import (
    AgentQueryBusyError,
    AgentQueryConfigNotFoundError,
    AgentQueryDecryptFailedError,
    AgentQueryError,
    AgentQueryExecutionFailedError,
    AgentQueryInvalidPayloadError,
    AgentQueryPlainDisabledError,
    AgentQueryResourceLimitError,
    AgentQueryUnsupportedScopeError,
)
from ..query.time_range import validate_time_range
from ..query_logger import get_event_query_logger
from ..services.agent_routing import (
    list_routable_space_agents,
)
from ..services.observation_subject_service import require_agent_subject
from ..trace_query.config import resolve_configured_agent_product
from ..trace_query.types import TraceQueryServiceError
from .common import check_space_access_or_error, error_response, success_response

logger = logging.getLogger(__name__)
_query_logger = get_event_query_logger()

_REQUIRED_PAYLOAD_FIELDS = {"scope", "queryKey", "timeRange"}
_REQUIRED_BATCH_PAYLOAD_FIELDS = {"scope", "timeRange", "queries"}
_REQUIRED_BATCH_QUERY_FIELDS = {"queryKey"}
_MAX_BATCH_QUERY_COUNT = 20

_VALID_SOURCE_SYSTEMS = frozenset({"signoz"})

_EMPTY_AGENT_PRODUCT_RE = re.compile(r"(AND\s+`[^`]+`\s*)=(\s*'')")


def _sanitize_source_system(value: str) -> str:
    return value if value in _VALID_SOURCE_SYSTEMS else "signoz"


def _find_signoz_datasource(agent_type_config, source_system="signoz"):
    """Find the signoz logs DataSource for an AgentTypeConfig."""
    source_system = _sanitize_source_system(source_system)
    return DataSource.objects.filter(
        agent_type=agent_type_config,
        source_system=source_system,
        datasource_type="logs",
        enabled=1,
        gmt_deleted__isnull=True,
    ).first()


def _find_space_agent_signoz_datasource(
    agent,
    source_system="signoz",
    *,
    active_statuses=(1,),
):
    """Recheck Space-Agent eligibility while selecting its datasource."""
    source_system = _sanitize_source_system(source_system)
    candidates = list(
        DataSource.objects.filter(
            agent_type=agent.agent_type,
            agent_type__status=1,
            agent_type__gmt_deleted__isnull=True,
            agent_type__agents__id=agent.id,
            agent_type__agents__space=agent.space,
            agent_type__agents__status__in=active_statuses,
            agent_type__agents__gmt_deleted__isnull=True,
            source_system=source_system,
            datasource_type="logs",
            enabled=1,
            gmt_deleted__isnull=True,
        )
        .order_by("-is_default", "id")
        .distinct()[:2]
    )
    return candidates[0] if len(candidates) == 1 else None


def _load_agent_query_route(agent_product_or_name, source_system="signoz"):
    """Load the AgentTypeConfig and V2 datasource for an agent identifier."""
    agent_type = (
        AgentTypeConfig.objects.active_shared_runtime()
        .filter(
            Q(agent_product=agent_product_or_name)
            | Q(agent_name=agent_product_or_name),
        )
        .order_by("id")
        .first()
    )

    if not agent_type:
        return {"agent_type": None, "datasource": None}

    return {
        "agent_type": agent_type,
        "datasource": _find_signoz_datasource(agent_type, source_system),
    }


def _find_agent_query_route(agent_product_or_name, source_system="signoz"):
    """Find an AgentTypeConfig and datasource by public product or internal name.

    AntCC and ClaudeCode share the same dashboard implementation, but their
    datasource configs are distinct.  The URL value may be the public product
    (``AntCC``) or the backend/internal name (``ANT_CLAUDE_CODE``), so resolve
    the database row exactly before reading datasource config.

    The V2 dashboard fires many section queries in parallel.  Cache this route
    lookup so one page render does not fan out into dozens of identical DB
    reads before each SigNoz request.

    ``source_system`` propagates into the cache key so signoz and
    signoz routes do not pollute each other.
    """
    safe_source = _sanitize_source_system(source_system)
    return cached_query(
        f"agent_type:v2_query_route:{agent_product_or_name}:{safe_source}",
        lambda: _load_agent_query_route(agent_product_or_name, safe_source),
    )


def _load_space_agent_query_route(space, agent_product_or_name, source_system="signoz"):
    """Resolve a query route through exactly one eligible Space Agent."""
    safe_source = _sanitize_source_system(source_system)
    active_statuses = (1, 2)
    agents = list_routable_space_agents(
        space,
        agent_product_or_name,
        active_statuses=active_statuses,
    )
    agent = agents[0] if agents else None
    if not agent:
        return {"agent": None, "agent_type": None, "datasource": None}

    agent_type = agent.agent_type
    return {
        "agent": agent,
        "agent_type": agent_type,
        "datasource": _find_space_agent_signoz_datasource(
            agent,
            safe_source,
            active_statuses=active_statuses,
        ),
    }


def _resolve_space_agent_type_or_error(user, space_id, agent_product):
    """Resolve a Space-scoped AgentTypeConfig for GET config endpoints.

    Routes through the Space→Agent→AgentTypeConfig chain used by POST
    /query, so space-scoped Agent types
    resolve correctly.  The caller only needs the AgentTypeConfig, not the
    preselected logs datasource, so this deliberately skips
    ``_find_space_agent_signoz_datasource`` — its "exactly one candidate"
    rule is too strict for the trace/event listing endpoints.

    Returns ``(space, agent_type, error_response)``.  ``error_response`` is
    ``None`` on success.  ``agent_type`` is ``None`` when the Space has no
    routable Agent for this product.
    """
    try:
        space = Space.objects.get(uuid=space_id, status=1)
    except Space.DoesNotExist:
        return None, None, error_response("Space not found", 3001, status=404)
    access_error = check_space_access_or_error(user, space)
    if access_error:
        return None, None, access_error
    agents = list_routable_space_agents(
        space,
        agent_product,
        active_statuses=(1, 2),
    )
    agent = agents[0] if agents else None
    agent_type = agent.agent_type if agent else None
    return space, agent_type, None


def _compute_query_config_payload(agent_type, source_system, *, space=None):
    """Build the non-sensitive query-config response payload."""
    agent_product_value = agent_type.agent_product
    display_scope = getattr(agent_type, "display_scope", "ALL")
    encryption_keys = getattr(django_settings, "AGENT_QUERY_ENCRYPTION_KEYS", {})
    enc_kid, enc_key_b64 = (
        next(iter(encryption_keys.items())) if encryption_keys else (None, None)
    )
    return {
        "agent_product": agent_product_value,
        "source_system": source_system,
        "capabilities": {
            "supports_personal_dashboard": display_scope in ("PERSONAL", "ALL"),
            "supports_space_dashboard": display_scope in ("BUSINESS", "ALL"),
            "supports_encrypted_query": is_encryption_enabled(),
            "supports_plain_query": is_plain_allowed(),
        },
        "kid": enc_kid,
        "keyB64": enc_key_b64,
    }


def _compute_datasource_config_payload(agent_type):
    """Build the trace/event datasource config payload for an AgentTypeConfig."""
    trace_ds_list = list(
        DataSource.objects.filter(
            agent_type=agent_type,
            datasource_type="traces",
            enabled=1,
            gmt_deleted__isnull=True,
        )
        .values_list("source_system", flat=True)
        .distinct()
    )

    source_set = set(trace_ds_list)
    trace_sources = sorted(source_set)

    default_source = None
    if len(source_set) > 1:
        default_ds = DataSource.objects.filter(
            agent_type=agent_type,
            datasource_type="traces",
            enabled=1,
            is_default=1,
            gmt_deleted__isnull=True,
        ).first()
        if default_ds:
            default_source = default_ds.source_system

    event_ds_list = list(
        DataSource.objects.filter(
            agent_type=agent_type,
            datasource_type="logs",
            enabled=1,
            gmt_deleted__isnull=True,
        )
        .values_list("source_system", flat=True)
        .distinct()
    )
    event_source_set = set(event_ds_list)
    event_sources = sorted(event_source_set)

    default_event_source = None
    if len(event_sources) == 1:
        default_event_source = event_sources[0]
    elif len(event_sources) > 1:
        default_event_ds = DataSource.objects.filter(
            agent_type=agent_type,
            datasource_type="logs",
            enabled=1,
            is_default=1,
            gmt_deleted__isnull=True,
        ).first()
        if default_event_ds:
            default_event_source = default_event_ds.source_system

    trace_sources, default_source, event_sources, default_event_source = (
        _filter_default_sources(
            trace_sources,
            default_source,
            event_sources,
            default_event_source,
        )
    )

    return {
        "traceSources": trace_sources,
        "defaultSource": default_source,
        "eventSources": event_sources,
        "defaultEventSource": default_event_source,
    }


class SpaceAgentQueryConfigView(APIView):
    """
    GET /api/v2/spaces/{space_id}/agent-products/{agent_product}/query-config

    Space-scoped query-config.  Replaces the deprecated
    ``GET /api/v2/agent-products/{agent_product}/query-config`` for spaces
    that host space-scoped Agent types. Resolution validates the Agent belongs
    to the requested Space.

    Returns 404 (AgentQueryConfigNotFoundError) when the Space has no routable
    Agent for this product — no silent fallback, unlike the legacy view.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id, agent_product):
        space, agent_type, error = _resolve_space_agent_type_or_error(
            request.user,
            space_id,
            agent_product,
        )
        if error is not None:
            return error
        if not agent_type:
            return AgentQueryConfigNotFoundError().to_response()
        return success_response(
            _compute_query_config_payload(agent_type, "unknown", space=space)
        )


class SpaceDatasourceConfigView(APIView):
    """
    GET /api/v2/spaces/{space_id}/agent-products/{agent_product}/datasource-config

    Space-scoped datasource-config.  Replaces the deprecated
    ``GET /api/v2/agent-products/{agent_product}/datasource-config`` for
    spaces hosting space-scoped Agent types.

    Returns 404 (AgentQueryConfigNotFoundError) when the Space has no routable
    Agent for this product — no silent langfuse/signoz fallback, unlike the
    legacy view.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id, agent_product):
        _, agent_type, error = _resolve_space_agent_type_or_error(
            request.user,
            space_id,
            agent_product,
        )
        if error is not None:
            return error
        if not agent_type:
            return AgentQueryConfigNotFoundError().to_response()
        return success_response(_compute_datasource_config_payload(agent_type))


class AgentQueryConfigView(APIView):
    """
    [DEPRECATED] GET /api/v2/agent-products/{agent_product}/query-config

    已被 ``GET /api/v2/spaces/{space_id}/agent-products/{agent_product}/query-config``
    取代。该路由仅在共享运行时中按 ``agent_product`` 解析；空间接口同时校验 Agent 归属。响应附带
    ``Deprecation: true`` 头。

    返回该 Agent 产品的非敏感查询能力。
    绝不暴露真实表名、pk/sk 字段名或查询 endpoint URL。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, agent_product):
        route = _find_agent_query_route(agent_product)
        agent_type = route["agent_type"]

        if not agent_type:
            response = error_response(
                f"Agent query config not found: {agent_product}",
                code=4001,
            )
        else:
            ds = route["datasource"]
            source_system = ds.source_system if ds else "unknown"
            response = success_response(
                _compute_query_config_payload(agent_type, source_system)
            )

        response["Deprecation"] = "true"
        return response


class DatasourceConfigView(APIView):
    """
    [DEPRECATED] GET /api/v2/agent-products/{agent_product}/datasource-config

    已被 ``GET /api/v2/spaces/{space_id}/agent-products/{agent_product}/datasource-config``
    取代。该路由仅在共享运行时中按 ``agent_product`` 解析；空间接口同时校验 Agent 归属。响应附带
    ``Deprecation: true`` 头。

    返回该 Agent 产品的 trace / event 数据源配置。
    traceSources: trace 数据源 source_system 列表（向后兼容）。
    defaultSource: trace 类 is_default=1 的 source_system。
    eventSources: event/logs 数据源 source_system 列表（dashboard 切换器消费）。
    defaultEventSource: event 类 is_default=1 的 source_system，无则 null。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, agent_product):
        route = _find_agent_query_route(agent_product)
        agent_type = route["agent_type"]

        if not agent_type:
            response = success_response(
                {
                    "traceSources": [],
                    "eventSources": [],
                    "defaultEventSource": None,
                }
            )
        else:
            response = success_response(_compute_datasource_config_payload(agent_type))

        response["Deprecation"] = "true"
        return response


def _filter_default_sources(
    trace_sources: list[str],
    default_source: str | None,
    event_sources: list[str],
    default_event_source: str | None,
) -> tuple[list[str], str | None, list[str], str | None]:
    """存在多数据源时只暴露服务端配置的默认源。"""
    if len(trace_sources) > 1 and default_source:
        trace_sources = [default_source]
    if len(event_sources) > 1 and default_event_source:
        event_sources = [default_event_source]
    return trace_sources, default_source, event_sources, default_event_source


class AgentDashboardQueryView(APIView):
    """
    POST /api/v2/spaces/{space_id}/agent-products/{agent_product}/query

    执行加密或明文查询模板，渲染后端模板变量，并代理查询到配置的数据源端点。
    """

    permission_classes = [IsAuthenticated]
    # Class-level source_system selector; subclasses override for routed datasources.
    source_system = "signoz"

    def post(self, request, space_id, agent_product):
        start = time.monotonic()
        user = request.user

        try:
            return self._handle_post(request, space_id, agent_product, user, start)
        except AgentQueryError as exc:
            return exc.to_response()

    def _handle_post(self, request, space_id, agent_product, user, start):
        from ..metrics import v2_query_count, v2_query_duration, v2_query_errors

        source_system = self.source_system

        context_or_response = self._resolve_query_context(
            user=user,
            space_id=space_id,
            agent_product=agent_product,
            source_system=source_system,
        )
        if not isinstance(context_or_response, dict):
            return context_or_response

        context = context_or_response

        payload, is_encrypted = self._read_request_payload(request)

        query_key = payload.get("queryKey", "unknown")
        metric_attrs = {
            "agent_product": agent_product,
            "query_key": query_key,
            "scope": payload.get("scope", "unknown"),
        }

        try:
            result = self._run_query_payload(
                payload=payload,
                context=context,
                agent_product=agent_product,
                space_id=space_id,
                is_encrypted=is_encrypted,
            )
        except Exception:
            v2_query_errors.add(1, metric_attrs)
            raise

        total_ms = int((time.monotonic() - start) * 1000)
        total_s = time.monotonic() - start
        v2_query_count.add(1, {**metric_attrs, "status": "success"})
        v2_query_duration.record(total_s, metric_attrs)

        logger.info(
            "AgentDashboardQueryView: space_id=%s agent_product=%s "
            "queryKey=%s scope=%s encrypted=%s source_system=%s query_time_ms=%d row_count=%d",
            space_id,
            agent_product,
            result["queryKey"],
            payload.get("scope"),
            is_encrypted,
            source_system,
            result["meta"]["query_time_ms"],
            len(result.get("rows", [])),
        )
        result["meta"]["total_time_ms"] = total_ms
        return success_response(result)

    @staticmethod
    def _resolve_query_context(
        *, user, space_id, agent_product, source_system="signoz"
    ):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(user, space)
        if access_error:
            return access_error

        route = _load_space_agent_query_route(
            space, agent_product, source_system=source_system
        )
        agent_type = route["agent_type"]

        if not agent_type:
            return AgentQueryConfigNotFoundError().to_response()

        ds = route["datasource"]
        if not ds:
            return AgentQueryConfigNotFoundError().to_response()

        return {
            "space": space,
            "user": user,
            "agent": route["agent"],
            "agent_type": agent_type,
            "datasource": ds,
            "ds_config": ds.config or {},
            "user_id": require_agent_subject(route["agent"]),
            "source_system": source_system,
        }

    @staticmethod
    def _read_request_payload(request):
        encrypted_param = request.query_params.get(
            "e",
            request.query_params.get("encrypted", "1"),
        ).lower()
        is_encrypted = encrypted_param in ("1", "true", "yes")

        if is_encrypted:
            if not is_encryption_enabled():
                raise AgentQueryDecryptFailedError(
                    message="Encrypted query is not enabled",
                )
            payload = decrypt_agent_query_payload(request.data)
        else:
            if not is_plain_allowed():
                raise AgentQueryPlainDisabledError()
            payload = request.data

        return payload, is_encrypted

    @classmethod
    def _validate_payload_shape(cls, payload):
        if not isinstance(payload, dict):
            raise AgentQueryInvalidPayloadError(
                message="Payload must be a JSON object",
            )

        missing = _REQUIRED_PAYLOAD_FIELDS - set(payload.keys())
        if missing:
            raise AgentQueryInvalidPayloadError(
                message=f"Missing required fields: {', '.join(sorted(missing))}",
                details={"missing_fields": sorted(missing)},
            )

    @classmethod
    def _validate_scope(cls, *, scope, agent_type, agent_product):
        display_scope = getattr(agent_type, "display_scope", "ALL")
        supports_personal = display_scope in ("PERSONAL", "ALL")
        supports_space = display_scope in ("BUSINESS", "ALL")

        if scope == "personal" and not supports_personal:
            raise AgentQueryUnsupportedScopeError(
                message=f"Scope 'personal' is not supported for {agent_product}",
                details={"scope": scope},
            )
        if scope == "space" and not supports_space:
            raise AgentQueryUnsupportedScopeError(
                message=f"Scope 'space' is not supported for {agent_product}",
                details={"scope": scope},
            )
        if scope not in ("personal", "space"):
            raise AgentQueryUnsupportedScopeError(
                message=f"Invalid scope: {scope!r}",
                details={"scope": scope},
            )

    @classmethod
    def _render_query_payload(cls, *, payload, context, agent_product):
        cls._validate_payload_shape(payload)

        scope = payload.get("scope")
        cls._validate_scope(
            scope=scope, agent_type=context["agent_type"], agent_product=agent_product
        )
        from_ms, to_ms = validate_time_range(payload.get("timeRange"))
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise AgentQueryInvalidPayloadError(message="params must be an object")
        return {
            "query_key": payload["queryKey"],
            "scope": scope,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "params": params,
        }

    @classmethod
    def _run_query_payload(
        cls,
        *,
        payload,
        context,
        agent_product,
        space_id,
        is_encrypted,
    ):
        prepared = cls._render_query_payload(
            payload=payload,
            context=context,
            agent_product=agent_product,
        )
        ds_config = context["ds_config"]
        endpoint = ds_config.get("endpoint") or ds_config.get("signoz_endpoint") or ""
        access_token = (
            ds_config.get("api_key")
            or ds_config.get("access_token")
            or ds_config.get("signoz_api_key")
            or ""
        )

        try:
            exec_start = time.monotonic()
            from ..query.framework import FRAMEWORK_QUERY_KEYS, query_framework
            if prepared["query_key"] in FRAMEWORK_QUERY_KEYS:
                result = query_framework(context, prepared, agent_product)
            else:
                result = cls._execute_query(
                    endpoint=endpoint,
                    access_token=access_token,
                    username=context.get("user_id", "unknown"),
                    space_id=space_id,
                    source_system=context.get("source_system", ""),
                    agent_product=(
                        resolve_configured_agent_product(ds_config) or agent_product
                    ),
                    query_key=prepared["query_key"],
                    scope=prepared.get("scope", ""),
                    from_ms=prepared["from_ms"],
                    to_ms=prepared["to_ms"],
                    params=prepared.get("params") or {},
                )
            exec_ms = int((time.monotonic() - exec_start) * 1000)
        except AgentQueryError:
            raise
        except TraceQueryServiceError as exc:
            if exc.code in (3036, 3037):
                error_type = AgentQueryResourceLimitError if exc.code == 3036 else AgentQueryBusyError
                raise error_type(message=exc.message, details={"trace_query_code": exc.code}) from exc
            raise AgentQueryExecutionFailedError(message=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "AgentDashboardQueryView query execution failed: "
                "space_id=%s agent_product=%s queryKey=%s error=%s",
                space_id,
                agent_product,
                prepared["query_key"],
                str(exc),
            )
            raise AgentQueryExecutionFailedError(
                message="Query execution failed",
            ) from exc

        return {
            "queryKey": prepared["query_key"],
            "columns": result.get("columns", []),
            "rows": result.get("rows", []),
            "meta": {
                "encrypted": is_encrypted,
                "query_time_ms": exec_ms,
            },
        }

    @staticmethod
    def _execute_query(
        *,
        endpoint: str,
        access_token: str,
        username: str = "unknown",
        space_id: str = "",
        source_system: str = "signoz",
        agent_product: str = "",
        query_key: str = "",
        scope: str = "",
        from_ms: int = 0,
        to_ms: int = 0,
        params: dict | None = None,
    ) -> dict:
        if not endpoint:
            raise ValueError("SigNoz endpoint not configured")
        from apps.core.trace_context import _get_trace_id_header

        headers = {"Content-Type": "application/json", **_get_trace_id_header()}
        if access_token:
            headers["SIGNOZ-API-KEY"] = access_token
        body = {
            "queryKey": query_key,
            "scope": scope,
            "from": from_ms,
            "to": to_ms,
            "space_id": space_id,
            "user": username if scope == "personal" else "",
            "agent_product": agent_product,
            "params": params or {},
        }
        response = requests.post(
            f"{endpoint.rstrip('/')}/api/v1/ai-vision/dashboard/query",
            json=body,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        if (
            isinstance(result, dict)
            and result.get("status") == "success"
            and "data" in result
        ):
            result = result["data"]
        if not isinstance(result, dict):
            raise ValueError("Invalid SigNoz query response")
        return {"columns": result.get("columns", []), "rows": result.get("rows", [])}


class AgentDashboardBatchQueryView(AgentDashboardQueryView):
    """
    POST /api/v2/spaces/{space_id}/agent-products/{agent_product}/query_batch

    Executes several dashboard query specs for one agent product/scope. Space
    access, agent route, datasource config, decryption, and scope support are
    resolved once. Individual query rendering/execution errors are returned
    under ``errors`` without failing the whole batch.
    """

    def post(self, request, space_id, agent_product):
        start = time.monotonic()
        user = request.user

        try:
            return self._handle_batch_post(
                request,
                space_id,
                agent_product,
                user,
                start,
            )
        except AgentQueryError as exc:
            return exc.to_response()

    def _handle_batch_post(self, request, space_id, agent_product, user, start):
        from ..metrics import v2_batch_duration, v2_query_count, v2_query_errors

        source_system = self.source_system

        context_or_response = self._resolve_query_context(
            user=user,
            space_id=space_id,
            agent_product=agent_product,
            source_system=source_system,
        )
        if not isinstance(context_or_response, dict):
            return context_or_response

        context = context_or_response
        payload, is_encrypted = self._read_request_payload(request)
        batch = self._validate_batch_payload(
            payload=payload,
            context=context,
            agent_product=agent_product,
        )

        results = {}
        errors = {}

        for index, query_spec in enumerate(batch["queries"]):
            query_key = self._extract_batch_query_key(query_spec, index)
            metric_attrs = {
                "agent_product": agent_product,
                "query_key": query_key,
                "scope": batch["scope"],
            }
            try:
                query_payload = self._build_batch_query_payload(
                    query_spec=query_spec,
                    index=index,
                    scope=batch["scope"],
                    time_range=batch["time_range"],
                )
                results[query_key] = self._run_query_payload(
                    payload=query_payload,
                    context=context,
                    agent_product=agent_product,
                    space_id=space_id,
                    is_encrypted=is_encrypted,
                )
                v2_query_count.add(1, {**metric_attrs, "status": "success"})
            except AgentQueryError as exc:
                errors[query_key] = self._serialize_query_error(exc)
                v2_query_errors.add(1, metric_attrs)

        total_s = time.monotonic() - start
        total_ms = int(total_s * 1000)
        v2_batch_duration.record(
            total_s,
            {
                "agent_product": agent_product,
                "scope": batch["scope"],
                "query_count": len(batch["queries"]),
            },
        )

        logger.info(
            "AgentDashboardBatchQueryView: space_id=%s agent_product=%s "
            "scope=%s encrypted=%s query_count=%d success_count=%d error_count=%d "
            "total_time_ms=%d",
            space_id,
            agent_product,
            batch["scope"],
            is_encrypted,
            len(batch["queries"]),
            len(results),
            len(errors),
            total_ms,
        )

        return success_response(
            {
                "results": results,
                "errors": errors,
                "meta": {
                    "encrypted": is_encrypted,
                    "query_count": len(batch["queries"]),
                    "success_count": len(results),
                    "error_count": len(errors),
                    "total_time_ms": total_ms,
                },
            }
        )

    @classmethod
    def _validate_batch_payload(cls, *, payload, context, agent_product):
        if not isinstance(payload, dict):
            raise AgentQueryInvalidPayloadError(
                message="Payload must be a JSON object",
            )

        missing = _REQUIRED_BATCH_PAYLOAD_FIELDS - set(payload.keys())
        if missing:
            raise AgentQueryInvalidPayloadError(
                message=f"Missing required fields: {', '.join(sorted(missing))}",
                details={"missing_fields": sorted(missing)},
            )

        queries = payload.get("queries")
        if not isinstance(queries, list) or not queries:
            raise AgentQueryInvalidPayloadError(
                message="queries must be a non-empty array",
                details={"field": "queries"},
            )
        if len(queries) > _MAX_BATCH_QUERY_COUNT:
            raise AgentQueryInvalidPayloadError(
                message=f"queries cannot exceed {_MAX_BATCH_QUERY_COUNT} items",
                details={
                    "field": "queries",
                    "max": _MAX_BATCH_QUERY_COUNT,
                },
            )

        scope = payload.get("scope")
        cls._validate_scope(
            scope=scope,
            agent_type=context["agent_type"],
            agent_product=agent_product,
        )

        seen = set()
        duplicates = []
        for query_spec in queries:
            query_key = (
                query_spec.get("queryKey") if isinstance(query_spec, dict) else None
            )
            if not query_key:
                continue
            if query_key in seen:
                duplicates.append(query_key)
            seen.add(query_key)

        if duplicates:
            raise AgentQueryInvalidPayloadError(
                message="Duplicate queryKey values are not allowed in a batch",
                details={"duplicate_query_keys": sorted(set(duplicates))},
            )

        return {
            "scope": scope,
            "time_range": payload.get("timeRange"),
            "queries": queries,
        }

    @staticmethod
    def _extract_batch_query_key(query_spec, index):
        if isinstance(query_spec, dict) and query_spec.get("queryKey"):
            return query_spec["queryKey"]
        return f"__query_{index}"

    @staticmethod
    def _build_batch_query_payload(*, query_spec, index, scope, time_range):
        if not isinstance(query_spec, dict):
            raise AgentQueryInvalidPayloadError(
                message="Each query must be a JSON object",
                details={"index": index},
            )

        missing_query_fields = _REQUIRED_BATCH_QUERY_FIELDS - set(query_spec.keys())
        if missing_query_fields:
            raise AgentQueryInvalidPayloadError(
                message=(
                    "Missing required query fields: "
                    f"{', '.join(sorted(missing_query_fields))}"
                ),
                details={
                    "index": index,
                    "missing_fields": sorted(missing_query_fields),
                },
            )

        return {
            "scope": scope,
            "timeRange": time_range,
            "queryKey": query_spec.get("queryKey"),
            "params": query_spec.get("params"),
        }

    @staticmethod
    def _serialize_query_error(exc):
        return {
            "type": exc.type_str,
            "message": exc.message,
            "details": exc.details,
            "status": exc.http_status,
        }


class AgentDashboardSigNozQueryView(AgentDashboardQueryView):
    """OceanBase-backed SigNoz routed dashboard endpoint."""

    source_system = "signoz"


class AgentDashboardSigNozBatchQueryView(AgentDashboardBatchQueryView):
    """Batch variant of the OceanBase-backed SigNoz dashboard endpoint."""

    source_system = "signoz"
