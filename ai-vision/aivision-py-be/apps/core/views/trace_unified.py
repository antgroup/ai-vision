"""Unified trace/session/span views — single View class per operation, source_system injected via as_view()."""

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
    success_response,
    error_response,
    check_space_access_or_error,
)
from .trace_handlers import merge_session_list_params, merge_trace_list_params

logger = logging.getLogger(__name__)


def _get_space_or_error(space_id: str):
    try:
        return Space.objects.get(uuid=space_id, status=1), None
    except Space.DoesNotExist:
        return None, error_response("Space not found", 3001)


def _check_access(request, space):
    return check_space_access_or_error(request.user, space)


def _handle_error(exc, method_label: str):
    if isinstance(exc, SpaceObservationCredentialDecryptionError):
        return error_response(str(exc), 3017)
    if isinstance(exc, TraceQueryServiceError):
        if exc.code in (3036, 3037):
            return Response({'code': exc.code, 'message': exc.message, 'data': None},
                            status=413 if exc.code == 3036 else 429,
                            headers={'Retry-After': '1'} if exc.code == 3037 else None)
        if exc.code == 3019:
            return Response({"code": exc.code, "message": exc.message, "data": None}, status=404)
        return error_response(exc.message, exc.code)
    if isinstance(exc, http_requests.RequestException):
        logger.error("%s error: %s", method_label, exc)
        return error_response(f"Failed to {method_label}: {exc}", 3012)
    raise exc


# ---------------------------------------------------------------------------
# Trace collection operations
# ---------------------------------------------------------------------------


class UnifiedTraceListView(APIView):
    """POST /{prefix}/traces/list

    响应中合并 facets 候选聚合，前端不再单独请求 /traces/facets。
    """
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        query_params = merge_trace_list_params(
            payload, dict(request.query_params.items())
        )

        svc = get_default_trace_query_service()
        try:
            data = svc.list_traces(
                space=space, user=request.user,
                query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "query traces")

        # 合并 facets 候选聚合到 trace list 响应
        if "facets" not in data:
            try:
                facets_result = svc.list_facets(
                    space=space, user=request.user,
                    query_params=query_params,
                    source_system_override=self.source_system,
                )
                data["facets"] = facets_result.get("data", {})
            except Exception:
                logger.warning("%s trace list facets aggregation failed", self.source_system, exc_info=True)
                data["facets"] = None

        return success_response(data)


class UnifiedTraceMetricsView(APIView):
    """Complete-window metrics using the same native query and usage contract."""
    source_system = "signoz"

    def get(self, request, space_id):
        return self.post(request, space_id)

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err
        try:
            params = {**dict(request.query_params.items()), **(request.data if isinstance(request.data, dict) else {})}
            return success_response(get_default_trace_query_service().query_metrics(
                space=space, user=request.user, query_params=params,
                source_system_override=self.source_system))
        except Exception as exc:
            return _handle_error(exc, "query trace metrics")


class UnifiedTraceFacetsView(APIView):
    """POST /{prefix}/traces/facets

    返回多维筛选候选聚合 {env, username, scene, tags}，每个维度 [{value, count}]。
    入参与 traces/list 一致的时间范围 + agent_product + 已选筛选条件
    （env/scene/username/tags），用于交叉过滤。
    """
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        query_params = dict(request.query_params.items())
        if self.source_system == "signoz":
            # Canonical facets must retain the exact frozen native list scope,
            # including Agent identity, content searches and structured filters.
            query_params = merge_trace_list_params(payload, query_params)
        else:
            for key in (
                "from", "to", "agent_product",
                "env", "scene", "username", "tags",
            ):
                value = payload.get(key)
                if value is not None:
                    query_params[key] = value

        try:
            data = get_default_trace_query_service().list_facets(
                space=space, user=request.user,
                query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "query trace facets")
        return success_response(data)


class UnifiedTraceIOBatchView(APIView):
    """POST /{prefix}/traces/io"""
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        trace_ids_raw = payload.get("traceIds", payload.get("trace_ids"))
        if not isinstance(trace_ids_raw, list):
            return error_response("traceIds must be a list", 3026)
        trace_ids = [i.strip() for i in trace_ids_raw if isinstance(i, str) and i.strip()]
        if not trace_ids:
            return error_response("traceIds must not be empty", 3026)
        if len(trace_ids) > 100:
            return error_response("traceIds supports at most 100 items", 3026)

        query_params = dict(request.query_params.items())
        for key in (
            "from", "to", "agent_product", "agent_uuid",
            "orderBy", "include_io", "preview_only",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_trace_io_batch(
                space=space, user=request.user,
                trace_ids=trace_ids, query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get trace IO")
        return success_response(data)


class UnifiedTraceUsageBatchView(APIView):
    """POST /{prefix}/traces/usage"""
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        trace_ids_raw = payload.get("traceIds", payload.get("trace_ids"))
        if not isinstance(trace_ids_raw, list):
            return error_response("traceIds must be a list", 3026)
        trace_ids = [i.strip() for i in trace_ids_raw if isinstance(i, str) and i.strip()]
        trace_ids = list(dict.fromkeys(trace_ids))
        if not trace_ids:
            return error_response("traceIds must not be empty", 3026)
        if len(trace_ids) > 100:
            return error_response("traceIds supports at most 100 items", 3026)

        query_params = dict(request.query_params.items())
        for key in ("from", "to", "agent_product", "agent_uuid", "orderBy"):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_trace_usage_batch(
                space=space, user=request.user,
                trace_ids=trace_ids, query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get trace usage")
        return success_response(data)


# ---------------------------------------------------------------------------
# Single trace operations
# ---------------------------------------------------------------------------


class UnifiedTraceDetailView(APIView):
    """GET /{prefix}/trace/{trace_id}/detail"""
    source_system = ""

    def get(self, request, space_id, trace_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        try:
            data = get_default_trace_query_service().get_trace_detail(
                space=space, user=request.user,
                trace_id=trace_id, query_params=request.query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get trace detail")
        return success_response(data)


# ---------------------------------------------------------------------------
# Session collection operations
# ---------------------------------------------------------------------------


class UnifiedSessionListView(APIView):
    """POST /{prefix}/sessions/list"""
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        query_params = merge_session_list_params(
            payload, dict(request.query_params.items())
        )

        try:
            data = get_default_trace_query_service().list_sessions(
                space=space, user=request.user,
                query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "query sessions")
        return success_response(data)


class UnifiedSessionIOBatchView(APIView):
    """POST /{prefix}/sessions/io"""
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        session_ids_raw = payload.get("sessionIds", payload.get("session_ids"))
        if not isinstance(session_ids_raw, list):
            return error_response("sessionIds must be a list", 3026)
        session_ids = [i.strip() for i in session_ids_raw if isinstance(i, str) and i.strip()]
        if not session_ids:
            return error_response("sessionIds must not be empty", 3026)
        if len(session_ids) > 100:
            return error_response("sessionIds supports at most 100 items", 3026)

        query_params = dict(request.query_params.items())
        for key in (
            "from", "to", "fromTimestamp", "toTimestamp",
            "agent_product", "agent_uuid", "session_semantics",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_session_io_batch(
                space=space, user=request.user,
                session_ids=session_ids, query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get session IO batch")
        return success_response(data)


class UnifiedSessionUsageBatchView(APIView):
    """POST /{prefix}/sessions/usage"""
    source_system = ""

    def post(self, request, space_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        session_ids_raw = payload.get("sessionIds", payload.get("session_ids"))
        if not isinstance(session_ids_raw, list):
            return error_response("sessionIds must be a list", 3026)
        session_ids = [i.strip() for i in session_ids_raw if isinstance(i, str) and i.strip()]
        session_ids = list(dict.fromkeys(session_ids))
        if not session_ids:
            return error_response("sessionIds must not be empty", 3026)
        if len(session_ids) > 100:
            return error_response("sessionIds supports at most 100 items", 3026)

        query_params = dict(request.query_params.items())
        for key in ("from", "to", "fromTimestamp", "toTimestamp", "agent_product", "agent_uuid", "session_semantics"):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_session_usage_batch(
                space=space, user=request.user,
                session_ids=session_ids, query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get session usage")
        return success_response(data)


# ---------------------------------------------------------------------------
# Single session operations
# ---------------------------------------------------------------------------


class UnifiedSessionDetailView(APIView):
    """GET /{prefix}/session/{session_id}/detail"""
    source_system = ""

    def get(self, request, space_id, session_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        try:
            data = get_default_trace_query_service().get_session_detail(
                space=space, user=request.user,
                session_id=session_id, query_params=request.query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get session detail")
        return success_response(data)


class UnifiedSessionIOView(APIView):
    """POST /{prefix}/session/{session_id}/io"""
    source_system = ""

    def post(self, request, space_id, session_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        query_params = dict(request.query_params.items())
        payload = request.data if isinstance(request.data, dict) else {}
        for key in ("from", "to", "fromTimestamp", "toTimestamp", "agent_product", "agent_uuid", "session_semantics"):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_session_io(
                space=space, user=request.user,
                session_id=session_id, query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get session IO")
        return success_response(data)


# ---------------------------------------------------------------------------
# Span operations (under single trace)
# ---------------------------------------------------------------------------


class UnifiedSpanListView(APIView):
    """POST /{prefix}/trace/{trace_id}/spans/list"""
    source_system = ""

    def post(self, request, space_id, trace_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        payload = request.data if isinstance(request.data, dict) else {}
        span_ids_raw = payload.get("spanIds", payload.get("span_ids"))
        if not isinstance(span_ids_raw, list):
            return error_response("spanIds must be a list", 3026)
        span_ids = [i.strip() for i in span_ids_raw if isinstance(i, str) and i.strip()]
        if not span_ids:
            return error_response("spanIds must not be empty", 3026)
        if len(span_ids) > 200:
            return error_response("spanIds supports at most 200 items", 3026)

        query_params = dict(request.query_params.items())
        for key in ("from", "to", "agent_product", "agent_uuid"):
            value = payload.get(key)
            if value not in (None, ""):
                query_params[key] = value

        try:
            data = get_default_trace_query_service().get_span_batch(
                space=space, user=request.user,
                trace_id=trace_id, span_ids=span_ids,
                query_params=query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get span list")
        return success_response(data)


class UnifiedSpanDetailView(APIView):
    """GET /{prefix}/trace/{trace_id}/span/{span_id}/detail"""
    source_system = ""

    def get(self, request, space_id, trace_id, span_id):
        space, err = _get_space_or_error(space_id)
        if err:
            return err
        access_err = _check_access(request, space)
        if access_err:
            return access_err

        try:
            data = get_default_trace_query_service().get_observation_detail(
                space=space, user=request.user,
                observation_id=span_id, trace_id=trace_id,
                query_params=request.query_params,
                source_system_override=self.source_system,
            )
        except Exception as exc:
            return _handle_error(exc, "get span detail")
        return success_response(data)
