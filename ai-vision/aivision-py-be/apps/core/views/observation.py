"""
外部兼容 API (/api/public/*)

透传 Langfuse 响应格式，不包装 {code, message, data}。
使用 UnifiedExternalAuthentication 进行统一外部 API 鉴权（Basic Auth / Bearer SpaceKey）。
"""

import logging

import requests as http_requests
from rest_framework import status as http_status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.authentication import UnifiedExternalAuthentication
from apps.core.metrics import external_api_calls
from apps.core.services.credential_service import decrypt_secret
from apps.core.throttling import UnifiedExternalRateThrottle
from apps.core.trace_query.defaults import get_default_trace_query_service
from apps.core.trace_query.routing import resolve_trace_source_system
from apps.core.trace_query.types import TraceQueryServiceError

logger = logging.getLogger(__name__)


class PublicAPIView(APIView):
    """公共 API 基类，确保 401/403 状态码语义正确。

    支持 Credential (Basic Auth) 和 SpaceKey (Bearer) 两种鉴权方式。
    SpaceKey 鉴权时自动从 Space 解析 observation_project 和 endpoint。
    """

    authentication_classes = [UnifiedExternalAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [UnifiedExternalRateThrottle]
    throttle_scope = "external_api"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if getattr(request, "auth_type", None) not in {"credential", "space_key"}:
            raise AuthenticationFailed("Authentication credentials were not provided.")
        self._resolve_observation_context(request)

    def _resolve_observation_context(self, request):
        if getattr(request, "auth_type", None) == "credential":
            credential = getattr(request, "auth", None)
            if credential is None:
                raise AuthenticationFailed("Authentication required")
            if getattr(credential, "scope", None) != "READ":
                raise PermissionDenied("Write key cannot access read-only API")
            return

        if getattr(request, "auth_type", None) != "space_key":
            return

        from apps.core.models import SpaceObservationProject

        space = getattr(request.auth, "space", None)
        if not space:
            raise AuthenticationFailed("Invalid Space Key")

        try:
            project = SpaceObservationProject.objects.select_related(
                "endpoint_config"
            ).get(space=space, gmt_deleted__isnull=True)
        except SpaceObservationProject.DoesNotExist:
            raise AuthenticationFailed(
                "Space has no observation project configured"
            ) from None
        except SpaceObservationProject.MultipleObjectsReturned:
            logger.error(
                "Multiple active observation projects configured for space_id=%s",
                getattr(space, "uuid", ""),
            )
            raise AuthenticationFailed(
                "Space has multiple observation projects configured"
            ) from None

        request.observation_credential = None
        request.observation_project = project
        request.observation_endpoint = project.endpoint_config

    def handle_exception(self, exc):
        if isinstance(exc, AuthenticationFailed):
            return Response(
                {"detail": str(exc.detail)},
                status=http_status.HTTP_401_UNAUTHORIZED,
            )
        if isinstance(exc, PermissionDenied):
            return Response(
                {"detail": str(exc.detail)}, status=http_status.HTTP_403_FORBIDDEN
            )
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        resp = super().finalize_response(request, response, *args, **kwargs)
        if (
            getattr(request, "auth_type", None)
            and hasattr(request, "observation_project")
            and resp.status_code not in (401, 403)
        ):
            try:
                space = request.observation_project.space
                url_name = getattr(
                    getattr(request, "resolver_match", None), "url_name", "unknown"
                )
                external_api_calls.add(
                    1,
                    {
                        "space_id": str(space.uuid),
                        "auth_type": request.auth_type,
                        "endpoint": url_name,
                        "agent_product": "",
                        "status": "success" if resp.status_code < 400 else "error",
                    },
                )
            except Exception:
                logger.debug("Failed to record public OpenAPI metric", exc_info=True)
        return resp


def _get_upstream_auth_header(project):
    """使用 READ credential 构建上游 Langfuse 的 Auth Header"""
    import base64

    read_cred = project.credentials.filter(
        scope="READ", status=1, gmt_deleted__isnull=True
    ).first()
    if not read_cred:
        return {}

    secret_key = decrypt_secret(read_cred.secret_ciphertext)
    credentials = base64.b64encode(
        f"{read_cred.public_key}:{secret_key}".encode()
    ).decode()
    return {"Authorization": f"Basic {credentials}"}


def _build_passthrough_params(query_params):
    """
    透传全部 GET 查询参数，并保留多值参数。

    Langfuse Public API 的 GET 参数会持续演进，兼容层不应再维护一份
    易过期的白名单；直接转发查询字符串更符合"兼容 API"的预期。
    不对 trace_id 做 SOFA 归一化——public API 直接透传给上游 Langfuse。
    """
    params = {}
    for key, values in query_params.lists():
        filtered_values = [value for value in values if value]
        if not filtered_values:
            continue
        params[key] = (
            filtered_values if len(filtered_values) > 1 else filtered_values[0]
        )
    return params


def _convert_public_trace_latency_to_milliseconds(data):
    """Convert native public trace latency fields from seconds to milliseconds."""
    if not isinstance(data, dict):
        return data

    converted = dict(data)
    latency = converted.get("latency")
    if isinstance(latency, (int, float)) and not isinstance(latency, bool):
        converted["latency"] = round(latency * 1000, 6)

    observations = converted.get("observations")
    if isinstance(observations, list):
        converted_observations = []
        for observation in observations:
            if not isinstance(observation, dict):
                converted_observations.append(observation)
                continue
            converted_observation = dict(observation)
            observation_latency = converted_observation.get("latency")
            if isinstance(observation_latency, (int, float)) and not isinstance(
                observation_latency, bool
            ):
                converted_observation["latency"] = round(
                    observation_latency * 1000,
                    6,
                )
            converted_observations.append(converted_observation)
        converted["observations"] = converted_observations

    return converted


class PublicProjectsView(PublicAPIView):
    """GET /api/public/projects for an explicitly provisioned Langfuse project."""

    def get(self, request):
        project = request.observation_project
        endpoint = request.observation_endpoint
        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/projects",
                params=_build_passthrough_params(request.query_params),
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException:
            logger.warning("Upstream project lookup failed", exc_info=True)
            return Response({"error": "Upstream service unavailable"}, status=502)


class PublicTraceListView(PublicAPIView):
    """GET /api/public/traces — 兼容 Langfuse Public API"""

    def get(self, request):
        project = request.observation_project
        endpoint = request.observation_endpoint

        params = _build_passthrough_params(request.query_params)

        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/traces",
                params=params,
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException as e:
            logger.error(f"Upstream trace list error: {e}")
            return Response({"error": "Upstream service unavailable"}, status=502)


class PublicTraceDetailView(PublicAPIView):
    """GET /api/public/traces/{trace_id} — 兼容 Langfuse Public API

    Public API 通过 credential 鉴权，user=None 不绑定用户。

    请求参数从 query string 透传。
    """

    def get(self, request, trace_id):
        return self._handle_detail(
            request=request,
            trace_id=trace_id,
            params=_build_passthrough_params(request.query_params),
        )

    def _handle_detail(self, *, request, trace_id, params):
        project = request.observation_project
        endpoint = request.observation_endpoint
        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/traces/{trace_id}",
                params=params,
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException as e:
            logger.error(f"Upstream trace detail error: {e}")
            return Response({"error": "Upstream service unavailable"}, status=502)


class PublicSessionListView(PublicAPIView):
    """GET /api/public/sessions — 兼容 Langfuse Public API"""

    def get(self, request):
        project = request.observation_project
        endpoint = request.observation_endpoint

        params = _build_passthrough_params(request.query_params)

        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/sessions",
                params=params,
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException as e:
            logger.error(f"Upstream session list error: {e}")
            return Response({"error": "Upstream service unavailable"}, status=502)


class PublicSessionDetailView(PublicAPIView):
    """GET /api/public/sessions/{session_id} — 兼容 Langfuse Public API"""

    def get(self, request, session_id):
        project = request.observation_project
        endpoint = request.observation_endpoint
        params = _build_passthrough_params(request.query_params)

        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/sessions/{session_id}",
                params=params,
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException as e:
            logger.error(f"Upstream session detail error: {e}")
            return Response({"error": "Upstream service unavailable"}, status=502)


class PublicObservationListView(PublicAPIView):
    """GET /api/public/observations — 兼容 Langfuse Public API"""

    def get(self, request):
        project = request.observation_project
        endpoint = request.observation_endpoint
        params = _build_passthrough_params(request.query_params)

        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/observations",
                params=params,
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException as e:
            logger.error(f"Upstream observation list error: {e}")
            return Response({"error": "Upstream service unavailable"}, status=502)


class PublicObservationDetailView(PublicAPIView):
    """GET /api/public/observations/{observation_id} — 兼容 Langfuse Public API"""

    def get(self, request, observation_id):
        project = request.observation_project
        endpoint = request.observation_endpoint
        params = _build_passthrough_params(request.query_params)

        try:
            resp = http_requests.get(
                f"{endpoint.base_url.rstrip('/')}/api/public/observations/{observation_id}",
                params=params,
                headers=_get_upstream_auth_header(project),
                timeout=30,
            )
            return Response(resp.json(), status=resp.status_code)
        except http_requests.RequestException as e:
            logger.error(f"Upstream observation detail error: {e}")
            return Response({"error": "Upstream service unavailable"}, status=502)
