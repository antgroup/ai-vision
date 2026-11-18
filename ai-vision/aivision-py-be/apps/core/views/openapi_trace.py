"""External OpenAPI trace query endpoints with datasource-aware routing."""

import json
import logging

from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from ..authentication import UnifiedExternalAuthentication
from ..cache import cached_query
from ..metrics import external_api_calls
from ..models import Agent, AgentTypeConfig
from ..query_logger import get_openapi_access_logger
from ..services.agent_routing import resolve_routable_space_agent
from ..services.session_share_link_service import (
    SessionShareLinkError,
    create_external_session_share_link,
    create_external_trace_share_link,
)
from ..throttling import UnifiedExternalRateThrottle
from ..trace_query.types import TraceQueryServiceError
from ..services.trace_query_service import get_default_trace_query_service
from .common import check_space_key_access, error_response, success_response
from .openapi_base import OpenAPIBaseView
from .trace_handlers import (
    enrich_detail_with_io,
    enrich_detail_with_session_io,
    enrich_list_with_io,
    enrich_list_with_session_io,
    enrich_list_with_session_usage,
    enrich_list_with_usage,
    handle_query_error,
    merge_batch_params,
    merge_session_list_params,
    merge_trace_list_params,
    parse_batch_ids,
    query_session_detail,
    query_session_io,
    query_session_io_batch,
    query_session_list,
    query_session_usage_batch,
    query_span_batch,
    query_span_detail,
    query_trace_detail,
    query_trace_list,
)

logger = logging.getLogger(__name__)
_openapi_access_logger = get_openapi_access_logger()

_SOURCE_SYSTEM_ALIASES = {
    "langfuse": "langfuse",
    "signoz": "signoz",
}

_handle_error = handle_query_error


def _source_system_override(request, payload=None):
    """Return an explicitly requested provider, otherwise defer to the default route.

    The path-scoped ``space_id`` and ``agent_product`` remain authoritative.  The
    optional selector only chooses between datasource routes configured for that
    agent; each provider still resolves and validates its own DataSource.
    """
    query_params = getattr(request, "query_params", None) or {}
    payload = payload if isinstance(payload, dict) else {}
    raw_value = (
        query_params.get("sourceSystem")
        or query_params.get("source_system")
        or payload.get("sourceSystem")
        or payload.get("source_system")
    )
    if raw_value in (None, ""):
        return None
    if not isinstance(raw_value, str):
        raise TraceQueryServiceError("sourceSystem must be a string", 3027)
    source_system = _SOURCE_SYSTEM_ALIASES.get(raw_value.strip().lower())
    if source_system is None:
        raise TraceQueryServiceError(
            f"Unsupported trace source system: {raw_value}",
            3027,
        )
    return source_system


def _auth_log_prefix(request) -> str:
    """Return a non-empty, non-secret auth identifier for access logs."""
    auth_obj = getattr(request, "auth", None)
    auth_type = getattr(request, "auth_type", None)
    if auth_type == "space_key":
        return (getattr(auth_obj, "key_prefix", "") or "space_key")[:20]
    if auth_type == "credential":
        public_key = getattr(auth_obj, "public_key", "")
        if public_key:
            return f"cred:{public_key[:15]}"[:20]
        return "cred:unknown"
    if auth_type == "user":
        user_uuid = getattr(getattr(request, "user", None), "uuid", "unknown")
        return f"user:{user_uuid}"[:20]
    return "unknown"


def _client_ip(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _request_path(request) -> str:
    return getattr(request, "path", "") or getattr(
        getattr(request, "_request", None), "path", ""
    )


def _log_access(request, space, agent_product, endpoint, metric_endpoint=None):
    metric_endpoint = metric_endpoint or endpoint
    auth_prefix = _auth_log_prefix(request)
    ip_address = _client_ip(request)
    try:
        external_api_calls.add(
            1,
            {
                "space_id": str(space.uuid),
                "auth_type": getattr(request, "auth_type", "unknown"),
                "endpoint": metric_endpoint,
                "agent_product": agent_product,
                "user_uuid": getattr(getattr(request, "user", None), "uuid", ""),
                "status": "success",
            },
        )
    except Exception:
        logger.exception("Failed to record OpenAPI access metric")

    try:
        _openapi_access_logger.info(
            json.dumps(
                {
                    "event": "openapi_access",
                    "auth_type": getattr(request, "auth_type", ""),
                    "auth_prefix": auth_prefix,
                    "space_id": str(getattr(space, "uuid", "")),
                    "agent_product": agent_product,
                    "endpoint": endpoint,
                    "endpoint_group": metric_endpoint,
                    "path": _request_path(request),
                    "method": getattr(request, "method", ""),
                    "ip_address": ip_address,
                    "user_uuid": getattr(getattr(request, "user", None), "uuid", ""),
                    "result": "success",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except Exception:
        logger.exception("Failed to write OpenAPI access file log")


def _get_space_key_query_user(request, space, agent_product):
    """Attach the provisioned Agent subject to the OpenAPI query context."""

    if getattr(request, "auth_type", None) != "space_key":
        return None
    user = getattr(request.auth, "creator", None) or getattr(space, "creator", None)
    if getattr(space, "pk", None) is None:
        return user
    agent = resolve_routable_space_agent(
        space,
        agent_product,
        active_statuses=(1, 2),
    )
    if agent is None:
        return user
    binding = agent.observation_subject_binding
    binding.user.observation_subject_id = binding.subject_id
    return binding.user


class OpenAPITraceListView(OpenAPIBaseView):
    """POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/traces/list"""

    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        payload = dict(request.data) if isinstance(request.data, dict) else {}
        payload["agent_product"] = agent_product
        params = merge_trace_list_params(payload, dict(request.query_params.items()))
        query_user = _get_space_key_query_user(request, space, agent_product)

        io_flag = request.query_params.get("io", "").lower() == "true"
        usage_flag = request.query_params.get("usage", "").lower() == "true"

        try:
            source_system = _source_system_override(request, payload)
            data = query_trace_list(space, query_user, params, source_system)
            if io_flag:
                enrich_list_with_io(data, space, query_user, params, source_system)
            if usage_flag:
                enrich_list_with_usage(data, space, query_user, params, source_system)
        except Exception as exc:
            return _handle_error(exc, "query traces")

        _log_access(request, space, agent_product, "traces/list")
        return success_response(data)


class OpenAPITraceMetricsView(OpenAPIBaseView):
    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        payload = dict(request.data) if isinstance(request.data, dict) else {}
        params = {**dict(request.query_params.items()), **payload, "agent_product": agent_product}
        try:
            query_user = _get_space_key_query_user(request, space, agent_product)
            result = get_default_trace_query_service().query_metrics(
                space=space, user=query_user, query_params=params,
                source_system_override=_source_system_override(request, payload) or "signoz")
        except Exception as exc:
            return _handle_error(exc, "query trace metrics")
        _log_access(request, space, agent_product, "traces/metrics")
        return success_response(result)


class OpenAPITraceDetailView(OpenAPIBaseView):
    """GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/detail"""

    def get(self, request, space_id, agent_product, trace_id):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        query_params = dict(request.query_params.items())
        query_params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)
        io_flag = request.query_params.get("io", "").lower() == "true"

        try:
            source_system = _source_system_override(request)
            data = query_trace_detail(
                space, query_user, trace_id, query_params, source_system
            )
            if io_flag and data:
                enrich_detail_with_io(
                    data, space, query_user, trace_id, query_params, source_system
                )
        except Exception as exc:
            return _handle_error(exc, "get trace detail")

        _log_access(
            request, space, agent_product, f"trace/{trace_id}/detail", "trace/detail"
        )
        return success_response(data)


class OpenAPISessionListView(OpenAPIBaseView):
    """POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/list"""

    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        payload = dict(request.data) if isinstance(request.data, dict) else {}
        payload["agent_product"] = agent_product
        params = merge_session_list_params(payload, dict(request.query_params.items()))
        query_user = _get_space_key_query_user(request, space, agent_product)

        io_flag = request.query_params.get("io", "").lower() == "true"
        usage_flag = request.query_params.get("usage", "").lower() == "true"

        try:
            source_system = _source_system_override(request, payload)
            data = query_session_list(space, query_user, params, source_system)
            if io_flag:
                enrich_list_with_session_io(
                    data, space, query_user, params, source_system
                )
            if usage_flag:
                enrich_list_with_session_usage(
                    data, space, query_user, params, source_system
                )
        except Exception as exc:
            return _handle_error(exc, "query sessions")

        _log_access(request, space, agent_product, "sessions/list")
        return success_response(data)


class OpenAPISessionIOBatchView(OpenAPIBaseView):
    """POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/io"""

    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        payload = request.data if isinstance(request.data, dict) else {}
        session_ids, id_err = parse_batch_ids(
            payload, "sessionIds", "session_ids", max_items=100
        )
        if id_err:
            return error_response(id_err, 3026)

        params = merge_batch_params(payload, dict(request.query_params.items()))
        params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)

        try:
            source_system = _source_system_override(request, payload)
            data = query_session_io_batch(
                space, query_user, session_ids, params, source_system
            )
        except Exception as exc:
            return _handle_error(exc, "get session IO batch")

        _log_access(request, space, agent_product, "sessions/io")
        return success_response(data)


class OpenAPISessionUsageBatchView(OpenAPIBaseView):
    """POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/usage"""

    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        payload = request.data if isinstance(request.data, dict) else {}
        session_ids, id_err = parse_batch_ids(
            payload, "sessionIds", "session_ids", max_items=100
        )
        if id_err:
            return error_response(id_err, 3026)
        session_ids = list(dict.fromkeys(session_ids))

        params = merge_batch_params(payload, dict(request.query_params.items()))
        params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)

        try:
            source_system = _source_system_override(request, payload)
            data = query_session_usage_batch(
                space, query_user, session_ids, params, source_system
            )
        except Exception as exc:
            return _handle_error(exc, "get session usage")

        _log_access(request, space, agent_product, "sessions/usage")
        return success_response(data)


class OpenAPISessionDetailView(OpenAPIBaseView):
    """GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/detail"""

    def get(self, request, space_id, agent_product, session_id):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        query_params = dict(request.query_params.items())
        query_params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)
        io_flag = request.query_params.get("io", "").lower() == "true"

        try:
            source_system = _source_system_override(request)
            data = query_session_detail(
                space, query_user, session_id, query_params, source_system
            )
            if io_flag and data:
                enrich_detail_with_session_io(
                    data, space, query_user, session_id, query_params, source_system
                )
        except Exception as exc:
            return _handle_error(exc, "get session detail")

        _log_access(
            request,
            space,
            agent_product,
            f"session/{session_id}/detail",
            "session/detail",
        )
        return success_response(data)


class OpenAPISessionIOView(OpenAPIBaseView):
    """POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/io"""

    def post(self, request, space_id, agent_product, session_id):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        payload = request.data if isinstance(request.data, dict) else {}
        params = merge_batch_params(payload, dict(request.query_params.items()))
        params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)

        try:
            source_system = _source_system_override(request, payload)
            data = query_session_io(
                space, query_user, session_id, params, source_system
            )
        except Exception as exc:
            return _handle_error(exc, "get session IO")

        _log_access(
            request, space, agent_product, f"session/{session_id}/io", "session/io"
        )
        return success_response(data)


class OpenAPISpanListView(OpenAPIBaseView):
    """POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/spans"""

    def post(self, request, space_id, agent_product, trace_id):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        payload = request.data if isinstance(request.data, dict) else {}
        span_ids, id_err = parse_batch_ids(
            payload, "spanIds", "span_ids", max_items=200
        )
        if id_err:
            return error_response(id_err, 3026)

        params = merge_batch_params(payload, dict(request.query_params.items()))
        params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)

        try:
            source_system = _source_system_override(request, payload)
            data = query_span_batch(
                space, query_user, trace_id, span_ids, params, source_system
            )
        except Exception as exc:
            return _handle_error(exc, "get span list")

        _log_access(
            request, space, agent_product, f"trace/{trace_id}/spans", "trace/spans"
        )
        return success_response(data)


class OpenAPISpanDetailView(OpenAPIBaseView):
    """GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/span/{span_id}"""

    def get(self, request, space_id, agent_product, trace_id, span_id):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        query_params = dict(request.query_params.items())
        query_params["agent_product"] = agent_product
        query_user = _get_space_key_query_user(request, space, agent_product)

        try:
            source_system = _source_system_override(request)
            data = query_span_detail(
                space, query_user, trace_id, span_id, query_params, source_system
            )
        except Exception as exc:
            return _handle_error(exc, "get span detail")

        _log_access(
            request,
            space,
            agent_product,
            f"trace/{trace_id}/span/{span_id}",
            "trace/span/detail",
        )
        return success_response(data)


class OpenAPISpaceAgentsListView(OpenAPIBaseView):
    """GET /openapi/v1/spaces/{space_id}/agents"""

    MAX_AGENTS = 200

    def get(self, request, space_id):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err

        if getattr(space, "status", None) != 1:
            return error_response("Space not found", 3001, status=404)

        agents = (
            Agent.objects.select_related("agent_type")
            .filter(space__uuid=space_id, status__in=[1, 2])
            .order_by("-gmt_create")[: self.MAX_AGENTS]
        )

        all_configs = cached_query(
            "agent_types:shared_runtime",
            lambda: list(AgentTypeConfig.objects.shared_runtime()),
        )
        config_by_id = {atc.id: atc for atc in all_configs}
        config_by_name = {atc.agent_name: atc for atc in all_configs}
        config_by_product = {atc.agent_product: atc for atc in all_configs}

        result = []
        for agent in agents:
            config = (
                agent.agent_type
                or config_by_id.get(agent.agent_type_id)
                or config_by_product.get(agent.agent_product)
                or config_by_name.get(agent.agent_product)
            )
            result.append(
                {
                    "agent_product": (
                        config.agent_product if config else agent.agent_product
                    ),
                    "agent_name": config.agent_name if config else agent.agent_product,
                    "display_name": config.display_name if config else None,
                    "description": config.description if config else None,
                    "status": agent.status,
                    "last_active_at": (
                        agent.last_active_at.isoformat()
                        if agent.last_active_at
                        else None
                    ),
                }
            )

        _log_access(request, space, "_all", "agents")
        return success_response(result)


class OpenAPISessionShareLinkView(OpenAPIBaseView):
    """Create a signed public-session URL using a SpaceKey."""

    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        operator = getattr(request.auth, "creator", None) or space.creator
        try:
            data = create_external_session_share_link(
                space=space,
                agent_product=agent_product,
                payload=request.data,
                operator=operator,
            )
        except SessionShareLinkError as exc:
            return error_response(exc.message, exc.code)
        return success_response(data)


class OpenAPITraceShareLinkView(OpenAPIBaseView):
    """Create a signed public-trace URL using a SpaceKey."""

    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        operator = getattr(request.auth, "creator", None) or space.creator
        try:
            data = create_external_trace_share_link(
                space=space,
                agent_product=agent_product,
                payload=request.data,
                operator=operator,
            )
        except SessionShareLinkError as exc:
            return error_response(exc.message, exc.code)
        return success_response(data)
