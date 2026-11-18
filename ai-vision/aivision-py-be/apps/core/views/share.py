"""分享链接视图：生成签名链接 + 通过签名链接获取数据"""

import logging
from datetime import datetime, timezone as dt_timezone

import requests as http_requests
from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from ..models import Space
from ..services.onboarding_demo import demo_agent_uuid, reserved_demo_agent_uuid
from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from ..services.agent_routing import (
    resolve_routable_space_agent,
    resolve_space_agent_for_query,
)
from ..trace_query.routing import resolve_trace_source_system
from ..trace_query.types import TraceProviderNotConfiguredError
from ..services.observation_subject_service import (
    require_agent_subject,
    require_default_subject,
)
from ..utils.share_signing import (
    build_share_payload,
    sign_share_payload,
    get_share_link_ttl,
    parse_and_verify_share,
    log_share_event,
)
from .common import (
    SpaceObservationCredentialDecryptionError,
    success_response,
    error_response,
    check_space_access_or_error,
)

logger = logging.getLogger(__name__)

PROTOCOL_TO_SOURCE_SYSTEM = {
    "lf": None,
    "signoz": "signoz",
}


class _ShareUser:
    """Lightweight user proxy for unauthenticated share endpoints."""

    def __init__(self, claims: dict):
        self.uuid = claims.get("uid", "anonymous")
        self.name = claims.get("u", "anonymous")
        self.observation_subject_id = claims.get("sub", "")


def _build_share_info(claims: dict) -> dict:
    return {
        "sharer_name": claims.get("u", ""),
        "created_at": datetime.fromtimestamp(
            claims["iat"], tz=dt_timezone.utc
        ).isoformat(),
        "expires_at": datetime.fromtimestamp(
            claims["exp"], tz=dt_timezone.utc
        ).isoformat(),
    }


def _share_query_params(claims: dict) -> dict:
    """Use only signed scope; unsigned viewer parameters cannot widen a share."""
    return {
        name: claims[claim]
        for name, claim in (
            ("agent_product", "a"),
            ("agent_uuid", "au"),
            ("from", "qf"),
            ("to", "qt"),
        )
        if claims.get(claim)
    }


def _enrich_native_session(
    service, data, *, space, user, query_params, source_system_override
):
    """Resolve a signed session completely; never turn a failed page into success."""
    trace_ids = data["traceIds"]
    if (
        any(not isinstance(value, str) or not value for value in trace_ids)
        or len(set(trace_ids)) != len(trace_ids)
        or data.get("traceCount", len(trace_ids)) != len(trace_ids)
    ):
        raise TraceQueryServiceError("Incomplete shared session trace identities", 3035)
    context = {
        "space": space,
        "user": user,
        "source_system_override": source_system_override,
    }
    rows, expected = {}, set(trace_ids)
    page, pages, page_size = 1, 1, min(200, max(1, len(trace_ids)))
    while trace_ids and page <= pages:
        result = service.list_traces(
            **context,
            query_params={
                **query_params,
                "filters": [{"field": "trace_id", "op": "in", "value": trace_ids}],
                "page": page,
                "limit": page_size,
                "orderBy": "start_time_unix_nano.asc",
            },
        )
        meta, items = result.get("meta", {}), result.get("data")
        if (
            not isinstance(items, list)
            or meta.get("page") != page
            or meta.get("limit") != page_size
            or meta.get("totalItems") != len(trace_ids)
            or meta.get("totalPages") != (len(trace_ids) + page_size - 1) // page_size
            or meta.get("complete") is not True
            or len(items) != min(page_size, len(trace_ids) - (page - 1) * page_size)
        ):
            raise TraceQueryServiceError("Incomplete shared session trace page", 3035)
        for item in items:
            identifier = item.get("trace_id") if isinstance(item, dict) else None
            if identifier not in expected or identifier in rows:
                raise TraceQueryServiceError(
                    "Incomplete or unexpected shared session trace", 3035
                )
            rows[identifier] = item
        pages, page = meta["totalPages"], page + 1
    if set(rows) != expected:
        raise TraceQueryServiceError("Incomplete shared session trace list", 3035)
    data["traces"] = [rows[identifier] for identifier in trace_ids]

    io = {}
    for offset in range(0, len(trace_ids), 100):
        batch = trace_ids[offset : offset + 100]
        result = service.get_trace_io_batch(
            **context, trace_ids=batch, query_params=query_params
        )
        items = result.get("data")
        if not isinstance(items, list) or len(items) != len(batch):
            raise TraceQueryServiceError("Incomplete shared session input/output", 3035)
        for item in items:
            identifier = item.get("id") if isinstance(item, dict) else None
            if identifier not in batch or identifier in io:
                raise TraceQueryServiceError(
                    "Unexpected shared session input/output", 3035
                )
            io[identifier] = item
    data["io"] = [io[identifier] for identifier in trace_ids]
    result = service.get_session_usage_batch(
        **context, session_ids=[data["sessionId"]], query_params=query_params
    )
    usages = result.get("data")
    if (
        not isinstance(usages, list)
        or len(usages) != 1
        or not isinstance(usages[0], dict)
        or usages[0].get("id") != data["sessionId"]
        or not isinstance(usages[0].get("usage"), dict)
        or result.get("meta", {}).get("complete", True) is not True
    ):
        raise TraceQueryServiceError("Incomplete shared session usage", 3035)
    # Match the authenticated session usage endpoint, including unknown values,
    # cost provenance and IO source/state metadata without re-projecting them.
    data["usage"] = usages[0]


class GenerateShareLinkView(APIView):
    """POST /api/v1/spaces/<space_id>/share-links/generate"""

    def post(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        resource_type = request.data.get("resource_type", "")
        resource_id = request.data.get("resource_id", "")
        agent_product = request.data.get("agent_product", "")
        agent_uuid = request.data.get("agent_uuid")
        protocol = request.data.get("protocol", "lf")
        query_from = request.data.get("from", "")
        query_to = request.data.get("to", "")

        if resource_type not in ("trace", "session"):
            return error_response("resource_type must be 'trace' or 'session'", 4001)
        if not resource_id:
            return error_response("resource_id is required", 4002)
        if not isinstance(agent_product, str):
            return error_response("agent_product must be a string", 4001)
        if not isinstance(protocol, str) or protocol not in PROTOCOL_TO_SOURCE_SYSTEM:
            return error_response("Unsupported share protocol", 4001)
        if agent_uuid not in (None, "") and (
            not isinstance(agent_uuid, str) or not agent_uuid.strip()
        ):
            return error_response("agent_uuid must be a nonempty string", 3027)

        if protocol == "lf":
            try:
                source_system = resolve_trace_source_system(space, agent_product)
            except TraceProviderNotConfiguredError as exc:
                return error_response(str(exc), exc.code)
            if source_system == "signoz":
                # The legacy/default protocol can route to native storage.
                # Freeze its real backend and Agent scope just like explicit
                # native callers, before signing an anonymous read capability.
                protocol = "signoz"

        agent = None
        if agent_uuid or protocol == "signoz":
            agent = (
                resolve_space_agent_for_query(
                    space, agent_product, agent_uuid, active_statuses=(1, 2)
                )
                if agent_uuid
                else resolve_routable_space_agent(
                    space, agent_product, active_statuses=(1, 2)
                )
            )
            if agent is None:
                return error_response(
                    "Select an active Agent in this Space with agent_uuid", 3027
                )
            # Even implicit single-Agent shares freeze the instance, so adding
            # another Agent later cannot expand the public session scope.
            agent_uuid = str(agent.uuid)
            agent_product = agent_product or agent.agent_product

        subject_id = request.user.uuid
        if space.type != 2:
            # Match personal trace queries: native Agents carry their collector
            # identity and do not need an additional SigNoz subject binding.
            subject_id = (
                require_agent_subject(agent)
                if protocol == "signoz" and agent is not None
                else require_default_subject(
                    space=space,
                    user=request.user,
                    source_system=PROTOCOL_TO_SOURCE_SYSTEM.get(protocol) or "langfuse",
                )
            )

        now_ts = int(timezone.now().timestamp())
        ttl = get_share_link_ttl()
        claims = {
            "t": resource_type,
            "id": resource_id,
            "s": space.id,
            "a": agent_product,
            "p": protocol,
            "iat": now_ts,
            "exp": now_ts + ttl,
            "u": request.user.name,
            "uid": request.user.uuid,
            "sub": subject_id,
            "v": 1,
        }
        if agent_uuid:
            claims["au"] = agent_uuid
        # Persist the viewer's current time window into the signed claims so the
        # public /share/data query reuses it. Without this, native SigNoz
        # session queries fall back to a 7-day window and blow the OSS file-scan
        # limit (code 3012). Optional: omitted when the caller has no window.
        if query_from:
            claims["qf"] = query_from
        if query_to:
            claims["qt"] = query_to

        payload = build_share_payload(claims)
        sig = sign_share_payload(payload)

        site_url = getattr(settings, "SITE_URL", "")
        url = f"{site_url}/share/{resource_type}?payload={payload}&sig={sig}"

        log_share_event("create", claims, request.user, request)

        return success_response(
            {
                "url": url,
                "expires_at": datetime.fromtimestamp(
                    claims["exp"], tz=dt_timezone.utc
                ).isoformat(),
            }
        )


class ShareDataView(APIView):
    """GET /api/v1/share/data?payload=...&sig=..."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        payload_raw = request.query_params.get("payload", "")
        sig = request.query_params.get("sig", "")

        try:
            claims = parse_and_verify_share(payload_raw, sig)
        except ValueError as exc:
            return error_response(
                "SHARE_SIGNATURE_INVALID",
                3023,
                status=401,
                data={"error_key": "SHARE_SIGNATURE_INVALID"},
            )

        try:
            space = Space.objects.get(id=claims["s"], status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        resource_type = claims["t"]
        resource_id = claims["id"]
        agent_product = claims.get("a", "")
        protocol = claims.get("p", "lf")
        source_system_override = PROTOCOL_TO_SOURCE_SYSTEM.get(protocol)
        share_user = _ShareUser(claims)

        query_params = _share_query_params(claims)

        service = get_default_trace_query_service()

        try:
            if resource_type == "trace":
                data = service.get_trace_detail(
                    space=space,
                    user=share_user,
                    trace_id=resource_id,
                    query_params=query_params,
                    source_system_override=source_system_override,
                )
                result = {
                    "resource_type": "trace",
                    "trace": data,
                    "share_info": _build_share_info(claims),
                    "agent_product": agent_product,
                    "space_id": str(space.uuid),
                    "protocol": protocol,
                }
            elif resource_type == "session":
                data = service.get_session_detail(
                    space=space,
                    user=share_user,
                    session_id=resource_id,
                    query_params=query_params,
                    source_system_override=source_system_override,
                )
                # For OTel sessions (traceIds format), enrich with trace list
                # and IO so the frontend can render without extra auth requests.
                trace_ids = data.get("traceIds")
                if isinstance(trace_ids, list):
                    _enrich_native_session(
                        service,
                        data,
                        space=space,
                        user=share_user,
                        query_params=query_params,
                        source_system_override=source_system_override,
                    )
                result = {
                    "resource_type": "session",
                    "session": data,
                    "share_info": _build_share_info(claims),
                    "agent_product": agent_product,
                    "space_id": str(space.uuid),
                    "protocol": protocol,
                }
            else:
                return error_response("Invalid resource type", 4001)
        except SpaceObservationCredentialDecryptionError as exc:
            return error_response(str(exc), 3017)
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Share data upstream error: %s", exc)
            return error_response(f"Failed to get data: {exc}", 3013)

        # Only the signed scope and the server's reserved Agent record can
        # label a public share as simulated.
        agent_uuid = query_params.get("agent_uuid")
        if (
            protocol == "signoz"
            and agent_uuid == demo_agent_uuid(space.uuid)
            and agent_uuid == reserved_demo_agent_uuid(space)
        ):
            result["simulated_usage"] = True

        log_share_event("view", claims, share_user, request)

        return success_response(result)


class ShareObservationView(APIView):
    """GET /api/v1/share/observations/<observation_id>?payload=...&sig=..."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, observation_id):
        payload_raw = request.query_params.get("payload", "")
        sig = request.query_params.get("sig", "")

        try:
            claims = parse_and_verify_share(payload_raw, sig)
        except ValueError as exc:
            return error_response(str(exc), 3023)

        if claims["t"] != "trace":
            return error_response(
                "Observation access requires a trace share link", 4003
            )

        try:
            space = Space.objects.get(id=claims["s"], status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        agent_product = claims.get("a", "")
        protocol = claims.get("p", "lf")
        source_system_override = PROTOCOL_TO_SOURCE_SYSTEM.get(protocol)
        trace_id = claims["id"]
        share_user = _ShareUser(claims)

        query_params = _share_query_params(claims)

        service = get_default_trace_query_service()

        try:
            trace_detail = service.get_trace_detail(
                space=space,
                user=share_user,
                trace_id=trace_id,
                query_params=query_params,
                source_system_override=source_system_override,
            )
        except (TraceQueryServiceError, http_requests.RequestException) as exc:
            logger.error("Share observation trace check error: %s", exc)
            return error_response(f"Failed to get trace detail: {exc}", 3013)

        # Langfuse providers return {"observations": [{"id": ...}]}; native
        # SigNoz providers return {"spans": [{"span_id": ...}]}. Accept both
        # shapes so observation drill-down works for every share protocol.
        observations = trace_detail.get("observations")
        spans = trace_detail.get("spans")
        belongs = (
            isinstance(observations, list)
            and any(
                isinstance(item, dict) and item.get("id") == observation_id
                for item in observations
            )
        ) or (
            isinstance(spans, list)
            and any(
                isinstance(item, dict) and item.get("span_id") == observation_id
                for item in spans
            )
        )
        if not belongs:
            return error_response(
                "Observation does not belong to the shared trace", 3025
            )

        try:
            data = service.get_observation_detail(
                space=space,
                user=share_user,
                observation_id=observation_id,
                query_params=query_params,
                trace_id=trace_id,
                source_system_override=source_system_override,
            )
        except (TraceQueryServiceError, http_requests.RequestException) as exc:
            logger.error("Share observation detail error: %s", exc)
            return error_response(f"Failed to get observation: {exc}", 3013)

        return success_response(data)


class ShareTraceDetailView(APIView):
    """GET /api/v1/share/trace/<trace_id>?payload=...&sig=...

    Returns full trace detail for drill-down from a session share page.
    Accepts both session and trace share links.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, trace_id):
        payload_raw = request.query_params.get("payload", "")
        sig = request.query_params.get("sig", "")

        try:
            claims = parse_and_verify_share(payload_raw, sig)
        except ValueError as exc:
            return error_response(str(exc), 3023)

        try:
            space = Space.objects.get(id=claims["s"], status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        resource_type = claims["t"]
        agent_product = claims.get("a", "")
        protocol = claims.get("p", "lf")
        source_system_override = PROTOCOL_TO_SOURCE_SYSTEM.get(protocol)
        share_user = _ShareUser(claims)

        query_params = _share_query_params(claims)

        service = get_default_trace_query_service()

        try:
            if resource_type == "session":
                session_id = claims["id"]
                session_data = service.get_session_detail(
                    space=space,
                    user=share_user,
                    session_id=session_id,
                    query_params=query_params,
                    source_system_override=source_system_override,
                )
                # Validate trace belongs to session.
                # Langfuse returns {"traces": [{"id": ...}, ...]},
                # SigNoz returns {"traceIds": ["id1", "id2", ...]}
                traces = session_data.get("traces", [])
                trace_ids_list = session_data.get("traceIds", [])
                trace_in_session = trace_id in trace_ids_list or any(
                    isinstance(t, dict)
                    and (t.get("id") == trace_id or t.get("traceId") == trace_id)
                    for t in traces
                )
                if not trace_in_session:
                    return error_response(
                        "Trace does not belong to the shared session", 3025
                    )
            elif resource_type == "trace":
                if claims["id"] != trace_id:
                    return error_response(
                        "Trace ID does not match the shared trace", 3025
                    )
            else:
                return error_response("Invalid resource type", 4001)

            data = service.get_trace_detail(
                space=space,
                user=share_user,
                trace_id=trace_id,
                query_params=query_params,
                source_system_override=source_system_override,
            )
        except SpaceObservationCredentialDecryptionError as exc:
            return error_response(str(exc), 3017)
        except TraceQueryServiceError as exc:
            return error_response(exc.message, exc.code)
        except http_requests.RequestException as exc:
            logger.error("Share trace detail upstream error: %s", exc)
            return error_response(f"Failed to get trace detail: {exc}", 3013)

        return success_response(data)
