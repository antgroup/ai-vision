"""Trace/Session/Observation 查询视图"""

import logging

import requests as http_requests
from django.db import DatabaseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Space
from ..services.model_exchange_rates import exchange_rate_status
from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from .common import (
    SpaceObservationCredentialDecryptionError,
    read_credential_error_response,
    check_space_access_or_error,
    error_response,
    success_response,
)
from .langfuse import (
    build_trace_link_query_params,
    resolve_trace_link_request,
)

logger = logging.getLogger(__name__)


def _share_signature_invalid_response():
    return error_response(
        "SHARE_SIGNATURE_INVALID",
        3023,
        status=401,
        data={"error_key": "SHARE_SIGNATURE_INVALID"},
    )


def _trace_detail_matches_requested_trace(trace_detail: dict, trace_id: str) -> bool:
    # Langfuse returns trace id as "id", while SigNoz providers return
    # it as "traceId". Accept both shapes for trace-link anti-confusion checks.
    detail_ids = {
        value
        for value in (
            trace_detail.get("id"),
            trace_detail.get("traceId"),
        )
        if isinstance(value, str) and value
    }
    return trace_id in detail_ids


def _signed_link_exchange_rates() -> dict | None:
    """Return display-only FX metadata after the link's trace is authorized."""
    try:
        snapshot = exchange_rate_status()
    except DatabaseError:
        logger.warning("Reference exchange rates unavailable for signed trace link")
        return None
    return {key: snapshot[key] for key in ("rates", "rateDate", "status")}


class SessionListView(APIView):
    """查询当前用户 Session 列表（基于 Langfuse Traces 聚合）"""

    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        try:
            data = get_default_trace_query_service().list_sessions(
                space=space,
                user=request.user,
                query_params=request.query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider session list error (%s)", type(e).__name__)
            return error_response("Failed to query trace sessions", 3014)
        return success_response(data)


class SessionDetailView(APIView):
    """查询单个 Session 详情（基于 Langfuse Traces 聚合）"""

    def get(self, request, space_id, session_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        try:
            filtered_data = get_default_trace_query_service().get_session_detail(
                space=space,
                user=request.user,
                session_id=session_id,
                query_params=request.query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider session detail error (%s)", type(e).__name__)
            return error_response("Failed to get trace session detail", 3015)
        return success_response(filtered_data)


class TraceListView(APIView):
    """查询 Trace 列表 — 固定走 Langfuse 协议。"""

    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        try:
            data = get_default_trace_query_service().list_traces(
                space=space,
                user=request.user,
                query_params=request.query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider API error (%s)", type(e).__name__)
            return error_response("Failed to query trace provider", 3012)
        return success_response(data)


class TraceIOBatchView(APIView):
    """批量查询 Trace 列表当前页的 input/output。"""

    def post(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        payload = request.data if isinstance(request.data, dict) else {}
        trace_ids_raw = payload.get("traceIds", payload.get("trace_ids"))
        if not isinstance(trace_ids_raw, list):
            return error_response("traceIds must be a list", 3026)

        trace_ids = [
            item.strip()
            for item in trace_ids_raw
            if isinstance(item, str) and item.strip()
        ]
        if not trace_ids:
            return error_response("traceIds must not be empty", 3026)
        if len(trace_ids) > 100:
            return error_response("traceIds supports at most 100 items", 3026)

        query_params = dict(request.query_params.items())
        for key in (
            "fromTimestamp",
            "toTimestamp",
            "agent_product",
            "orderBy",
            "include_io",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_trace_io_batch(
                space=space,
                user=request.user,
                trace_ids=trace_ids,
                query_params=query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider IO batch error (%s)", type(e).__name__)
            return error_response("Failed to get trace input/output", 3012)
        return success_response(data)


class TraceUsageBatchView(APIView):
    """批量查询 Trace 列表当前页的 token usage。"""

    def post(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        payload = request.data if isinstance(request.data, dict) else {}
        trace_ids_raw = payload.get("traceIds", payload.get("trace_ids"))
        if not isinstance(trace_ids_raw, list):
            return error_response("traceIds must be a list", 3026)

        trace_ids = [
            item.strip()
            for item in trace_ids_raw
            if isinstance(item, str) and item.strip()
        ]
        trace_ids = list(dict.fromkeys(trace_ids))
        if not trace_ids:
            return error_response("traceIds must not be empty", 3026)
        if len(trace_ids) > 100:
            return error_response("traceIds supports at most 100 items", 3026)

        query_params = dict(request.query_params.items())
        for key in (
            "fromTimestamp",
            "toTimestamp",
            "agent_product",
            "orderBy",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_trace_usage_batch(
                space=space,
                user=request.user,
                trace_ids=trace_ids,
                query_params=query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider usage batch error (%s)", type(e).__name__)
            return error_response("Failed to get trace token usage", 3012)
        return success_response(data)


class TraceDetailView(APIView):
    """查询单个 Langfuse Trace 详情"""

    def get(self, request, space_id, trace_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        try:
            data = get_default_trace_query_service().get_trace_detail(
                space=space,
                user=request.user,
                trace_id=trace_id,
                query_params=request.query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            if exc.code == 3019:
                return Response(
                    {"code": exc.code, "message": exc.message, "data": None}
                )
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider detail error (%s)", type(e).__name__)
            return error_response("Failed to get trace detail", 3013)
        return success_response(data)


class TraceLinkDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        payload_raw = request.query_params.get("payload", "")
        sig = request.query_params.get("sig", "")

        try:
            space, claims, lf_config = resolve_trace_link_request(payload_raw, sig)
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError:
            return error_response("Agent query route not found", 3011)
        except ValueError:
            return _share_signature_invalid_response()

        trace_id = claims["trace_id"]
        try:
            trace_detail = get_default_trace_query_service().get_trace_detail(
                space=space,
                user=request.user,
                trace_id=trace_id,
                query_params=build_trace_link_query_params(claims),
            )
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Trace link detail upstream error (%s)", type(exc).__name__)
            return error_response("Failed to get trace detail", 3013)

        if not _trace_detail_matches_requested_trace(trace_detail, trace_id):
            return error_response(
                "Trace detail does not match the requested trace", 3024
            )

        return success_response(
            {
                "trace": trace_detail,
                "langfuse_config": {
                    "show_langfuse_link": lf_config.get("show_langfuse_link", False),
                    "project_url": lf_config.get("project_url", ""),
                },
                "agent_product": claims.get("agent_product", ""),
                "space_id": claims["space_id"],
                "exchange_rates": _signed_link_exchange_rates(),
            }
        )


class TraceLinkObservationDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, observation_id):
        payload_raw = request.query_params.get("payload", "")
        sig = request.query_params.get("sig", "")

        try:
            space, claims, _lf_config = resolve_trace_link_request(payload_raw, sig)
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError:
            return error_response("Agent query route not found", 3011)
        except ValueError:
            return _share_signature_invalid_response()

        trace_id = claims["trace_id"]
        try:
            query_params = build_trace_link_query_params(
                claims,
                {"trace_id": trace_id},
            )
            trace_detail = get_default_trace_query_service().get_trace_detail(
                space=space,
                user=request.user,
                trace_id=trace_id,
                query_params=query_params,
            )
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Trace link observation upstream trace check error (%s)", type(exc).__name__)
            return error_response("Failed to get trace detail", 3013)

        observations = trace_detail.get("observations")
        if not isinstance(observations, list) or not any(
            isinstance(item, dict) and item.get("id") == observation_id
            for item in observations
        ):
            return error_response(
                "Observation does not belong to the requested trace", 3025
            )

        try:
            data = get_default_trace_query_service().get_observation_detail(
                space=space,
                user=request.user,
                observation_id=observation_id,
                query_params=query_params,
            )
            return success_response(data)
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Trace link observation detail upstream error (%s)", type(exc).__name__)
            return error_response("Failed to get observation detail", 3014)


class ObservationLinkDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        payload_raw = request.query_params.get("payload", "")
        sig = request.query_params.get("sig", "")

        try:
            space, claims, lf_config = resolve_trace_link_request(payload_raw, sig)
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError:
            return error_response("Agent query route not found", 3011)
        except ValueError:
            return _share_signature_invalid_response()

        trace_id = claims["trace_id"]
        observation_id = claims.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            return error_response(
                "Missing required payload field: observation_id", 3023
            )

        query_params = build_trace_link_query_params(
            claims,
            {"trace_id": trace_id},
        )
        try:
            trace_detail = get_default_trace_query_service().get_trace_detail(
                space=space,
                user=request.user,
                trace_id=trace_id,
                query_params=query_params,
            )
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Observation link trace detail upstream error (%s)", type(exc).__name__)
            return error_response("Failed to get trace detail", 3013)

        observations = trace_detail.get("observations")
        if not isinstance(observations, list) or not any(
            isinstance(item, dict) and item.get("id") == observation_id
            for item in observations
        ):
            return error_response(
                "Observation does not belong to the requested trace",
                3025,
            )

        try:
            observation_detail = (
                get_default_trace_query_service().get_observation_detail(
                    space=space,
                    user=request.user,
                    observation_id=observation_id,
                    query_params=query_params,
                )
            )
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Observation link detail upstream error (%s)", type(exc).__name__)
            return error_response("Failed to get observation detail", 3014)

        return success_response(
            {
                "trace": trace_detail,
                "observation": observation_detail,
                "selected_observation_id": observation_id,
                "langfuse_config": {
                    "show_langfuse_link": lf_config.get("show_langfuse_link", False),
                    "project_url": lf_config.get("project_url", ""),
                },
                "agent_product": claims.get("agent_product", ""),
                "space_id": claims["space_id"],
                "exchange_rates": _signed_link_exchange_rates(),
            }
        )


class ObservationDetailView(APIView):
    """查询单个 Langfuse Observation 详情"""

    def get(self, request, space_id, observation_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        try:
            data = get_default_trace_query_service().get_observation_detail(
                space=space,
                user=request.user,
                observation_id=observation_id,
                query_params=request.query_params,
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("Trace provider observation detail error (%s)", type(e).__name__)
            return error_response("Failed to get observation detail", 3014)
        return success_response(data)
