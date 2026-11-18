from __future__ import annotations

from datetime import UTC, datetime, timedelta

import requests as http_requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.models import Agent
from apps.core.services.agent_routing import resolve_routable_space_agent
from apps.core.services.observation_subject_service import require_default_subject
from apps.core.trace_query.defaults import get_default_trace_query_service
from apps.core.trace_query.routing import get_trace_datasource
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.utils.share_signing import (
    build_share_payload,
    get_share_link_ttl,
    log_share_event,
    sign_share_payload,
)

SIGNOZ_SOURCE_SYSTEM = "signoz"
SIGNOZ_SHARE_PROTOCOL = "signoz"
TRACE_SHARE_MAX_WINDOW = timedelta(hours=24)


class SessionShareLinkError(Exception):
    def __init__(self, message: str, code: int = 4000):
        super().__init__(message)
        self.message = message
        self.code = code


def _get_space_agent(space: object, agent_product: str) -> Agent:
    agent = resolve_routable_space_agent(space, agent_product)
    if not agent:
        raise SessionShareLinkError("Agent type not found", 1006)
    return agent


def _resolve_share_trace_source(
    *, space: object, agent_product: str
) -> tuple[str, str]:
    """Use the active/default native-shaped trace backend for share links."""
    try:
        datasource = get_trace_datasource(space, agent_product=agent_product)
    except TraceQueryServiceError as exc:
        raise SessionShareLinkError(exc.message, exc.code) from exc
    if datasource is None:
        raise SessionShareLinkError(
            "Trace datasource is not configured for this agent_product",
            3011,
        )

    source_system = getattr(datasource, "source_system", "")
    protocol_by_source = {
        SIGNOZ_SOURCE_SYSTEM: SIGNOZ_SHARE_PROTOCOL,
    }
    protocol = protocol_by_source.get(source_system)
    if protocol is None:
        raise SessionShareLinkError(
            "The configured trace datasource does not support external share links",
            3011,
        )
    return source_system, protocol


def create_external_session_share_link(
    *,
    space: object,
    agent_product: str,
    payload: object,
    operator: object,
) -> dict:
    if not isinstance(payload, dict):
        raise SessionShareLinkError("request body must be an object", 4000)
    if set(payload) - {"session_id"}:
        raise SessionShareLinkError("Only session_id is supported", 4000)

    raw_session_id = payload.get("session_id")
    if not isinstance(raw_session_id, str) or not raw_session_id.strip():
        raise SessionShareLinkError("session_id is required", 4000)
    session_id = raw_session_id.strip()

    agent = _get_space_agent(space, agent_product)
    canonical_agent_product = agent.agent_type.agent_product
    source_system, share_protocol = _resolve_share_trace_source(
        space=space,
        agent_product=canonical_agent_product,
    )

    query_params = {"agent_product": canonical_agent_product}
    service = get_default_trace_query_service()
    try:
        session = service.get_session_detail(
            space=space,
            user=operator,
            session_id=session_id,
            query_params=query_params,
            source_system_override=source_system,
        )
    except TraceQueryServiceError as exc:
        raise SessionShareLinkError(exc.message, exc.code) from exc
    except http_requests.RequestException as exc:
        raise SessionShareLinkError("Failed to query session", 3013) from exc

    trace_ids = session.get("traceIds") if isinstance(session, dict) else None
    if not isinstance(trace_ids, list) or not trace_ids:
        raise SessionShareLinkError("Session not found", 3016)

    time_range = session.get("timeRange")
    query_from = time_range.get("from") if isinstance(time_range, dict) else None
    query_to = time_range.get("to") if isinstance(time_range, dict) else None
    if not isinstance(query_from, str) or not query_from:
        raise SessionShareLinkError("Session time range not found", 3016)
    if not isinstance(query_to, str) or not query_to:
        raise SessionShareLinkError("Session time range not found", 3016)

    site_url = str(getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if not site_url:
        raise SessionShareLinkError("SITE_URL is not configured", 3011)

    now_ts = int(timezone.now().timestamp())
    ttl = get_share_link_ttl()
    operator_name = getattr(operator, "name", "") or str(operator)
    operator_id = getattr(operator, "uuid", "") or str(operator)
    claims = {
        "t": "session",
        "id": session_id,
        "s": space.id,
        "a": canonical_agent_product,
        "p": share_protocol,
        "qf": query_from,
        "qt": query_to,
        "iat": now_ts,
        "exp": now_ts + ttl,
        "u": operator_name,
        "uid": operator_id,
        "sub": (
            require_default_subject(
                space=space,
                user=operator,
                source_system=source_system,
            )
            if getattr(space, "type", None) != 2
            else operator_id
        ),
        "v": 1,
    }
    share_payload = build_share_payload(claims)
    signature = sign_share_payload(share_payload)
    url = f"{site_url}/share/session?payload={share_payload}&sig={signature}"

    log_share_event("create", claims, operator)
    return {
        "url": url,
        "expires_at": datetime.fromtimestamp(claims["exp"], tz=UTC).isoformat(),
    }


def _parse_trace_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SessionShareLinkError(f"{field} is required", 4000)
    parsed = parse_datetime(value.strip())
    if parsed is None or parsed.utcoffset() is None:
        raise SessionShareLinkError(
            f"{field} must be an ISO 8601 datetime with timezone", 4000
        )
    return parsed


def create_external_trace_share_link(
    *,
    space: object,
    agent_product: str,
    payload: object,
    operator: object,
) -> dict:
    if not isinstance(payload, dict):
        raise SessionShareLinkError("request body must be an object", 4000)
    if set(payload) - {"trace_id", "from", "to"}:
        raise SessionShareLinkError("Only trace_id, from and to are supported", 4000)

    raw_trace_id = payload.get("trace_id")
    if not isinstance(raw_trace_id, str) or not raw_trace_id.strip():
        raise SessionShareLinkError("trace_id is required", 4000)
    trace_id = raw_trace_id.strip()
    query_from = _parse_trace_time(payload.get("from"), "from")
    query_to = _parse_trace_time(payload.get("to"), "to")
    if query_to <= query_from:
        raise SessionShareLinkError("to must be later than from", 4000)
    if query_to - query_from > TRACE_SHARE_MAX_WINDOW:
        raise SessionShareLinkError(
            "Trace share time range cannot exceed 24 hours", 4000
        )

    agent = _get_space_agent(space, agent_product)
    canonical_agent_product = agent.agent_type.agent_product
    source_system, share_protocol = _resolve_share_trace_source(
        space=space,
        agent_product=canonical_agent_product,
    )

    query_params = {
        "agent_product": canonical_agent_product,
        "from": query_from.isoformat(),
        "to": query_to.isoformat(),
    }
    service = get_default_trace_query_service()
    try:
        trace = service.get_trace_detail(
            space=space,
            user=operator,
            trace_id=trace_id,
            query_params=query_params,
            source_system_override=source_system,
        )
    except TraceQueryServiceError as exc:
        raise SessionShareLinkError(exc.message, exc.code) from exc
    except http_requests.RequestException as exc:
        raise SessionShareLinkError("Failed to query trace", 3013) from exc

    spans = trace.get("spans") if isinstance(trace, dict) else None
    if not isinstance(spans, list) or not spans:
        raise SessionShareLinkError("Trace not found", 3019)

    site_url = str(getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if not site_url:
        raise SessionShareLinkError("SITE_URL is not configured", 3011)

    now_ts = int(timezone.now().timestamp())
    ttl = get_share_link_ttl()
    operator_name = getattr(operator, "name", "") or str(operator)
    operator_id = getattr(operator, "uuid", "") or str(operator)
    claims = {
        "t": "trace",
        "id": trace_id,
        "s": space.id,
        "a": canonical_agent_product,
        "p": share_protocol,
        "qf": query_from.isoformat(),
        "qt": query_to.isoformat(),
        "iat": now_ts,
        "exp": now_ts + ttl,
        "u": operator_name,
        "uid": operator_id,
        "sub": (
            require_default_subject(
                space=space,
                user=operator,
                source_system=source_system,
            )
            if getattr(space, "type", None) != 2
            else operator_id
        ),
        "v": 1,
    }
    share_payload = build_share_payload(claims)
    signature = sign_share_payload(share_payload)
    url = f"{site_url}/share/trace?payload={share_payload}&sig={signature}"

    log_share_event("create", claims, operator)
    return {
        "url": url,
        "expires_at": datetime.fromtimestamp(claims["exp"], tz=UTC).isoformat(),
    }
