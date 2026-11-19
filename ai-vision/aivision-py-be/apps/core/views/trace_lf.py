"""Langfuse trace views — forces langfuse provider regardless of default datasource."""

import logging

import requests as http_requests
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Space
from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from .common import (
    SpaceObservationCredentialDecryptionError,
    read_credential_error_response,
    success_response,
    error_response,
    check_space_access_or_error,
)

logger = logging.getLogger(__name__)


class LfTraceListView(APIView):
    """POST 查询 Langfuse 轨迹列表，强制走 langfuse provider。

    响应中合并 facets 候选聚合，前端不再单独请求 /traces/facets。
    """

    def post(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        payload = request.data if isinstance(request.data, dict) else {}
        query_params = dict(request.query_params.items())
        for key in (
            "page",
            "limit",
            "fromTimestamp",
            "toTimestamp",
            "agent_product",
            "orderBy",
            "input_keyword",
            "keyword",
            "trace_id",
            "traceId",
            "tag",
            "env",
            "scene",
            "username",
            "tags",
        ):
            value = payload.get(key)
            if value is not None:
                query_params[key] = value

        svc = get_default_trace_query_service()
        try:
            data = svc.list_traces(
                space=space,
                user=request.user,
                query_params=query_params,
                source_system_override="langfuse",
            )
        except SpaceObservationCredentialDecryptionError:
            return read_credential_error_response()
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("LF trace list error (%s)", type(e).__name__)
            return error_response("Failed to query traces", 3012)

        # 合并 facets 候选聚合到 trace list 响应，前端不再单独请求
        try:
            facets_result = svc.list_facets(
                space=space,
                user=request.user,
                query_params=query_params,
                source_system_override="langfuse",
            )
            data["facets"] = facets_result.get("data", {})
        except Exception:
            logger.warning("LF trace list facets aggregation failed", exc_info=True)
            data["facets"] = None

        return success_response(data)


class LfTraceIOBatchView(APIView):
    """批量查询 Langfuse trace 的 input/output。"""

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
            logger.error("LF trace IO batch error (%s)", type(e).__name__)
            return error_response("Failed to get trace input/output", 3012)
        return success_response(data)


class LfTraceUsageBatchView(APIView):
    """批量查询 Langfuse trace 的 token usage。"""

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
            logger.error("LF trace usage batch error (%s)", type(e).__name__)
            return error_response("Failed to get trace token usage", 3012)
        return success_response(data)


class LfTraceDetailView(APIView):
    """GET 查询单个 Langfuse trace 详情。"""

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
                return Response({"code": exc.code, "message": exc.message, "data": None})
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as e:
            logger.error("LF trace detail error (%s)", type(e).__name__)
            return error_response("Failed to get trace detail", 3013)
        return success_response(data)


class LfSessionListView(APIView):
    """查询 Langfuse session 列表。"""

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
            logger.error("LF session list error (%s)", type(e).__name__)
            return error_response("Failed to query sessions", 3014)
        return success_response(data)


class LfSessionDetailView(APIView):
    """GET 查询单个 Langfuse session 详情。"""

    def get(self, request, space_id, session_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        try:
            data = get_default_trace_query_service().get_session_detail(
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
            logger.error("LF session detail error (%s)", type(e).__name__)
            return error_response("Failed to get session detail", 3015)
        return success_response(data)
