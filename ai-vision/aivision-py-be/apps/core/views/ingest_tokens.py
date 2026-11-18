import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.authentication import AgentIngestAuthentication
from apps.core.models import Agent, AgentInstallation, SpaceKey
from apps.core.permissions.constants import AGENT_INGEST_TOKEN_MINT
from apps.core.permissions.space import require_space_permission
from apps.core.services.agent_installation import (
    active_ingest_agents,
    ingest_credential_is_active,
    market_agents,
)
from apps.core.services.ingest_token import IngestTokenError, build_ingest_token
from apps.core.services.observation_subject_service import (
    ObservationSubjectError,
    require_agent_subject,
)
from apps.core.services.personal_diagnosis import sync_personal_diagnosis_after_commit
from apps.core.services.signoz_integration import current_ingest_org_id

from .common import error_response, success_response

logger = logging.getLogger(__name__)


class AgentLensIngestTokenView(APIView):
    """Service-only credential issuance; never accepts a browser session or SpaceKey."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        expected = getattr(settings, "AGENTLENS_TOKEN", "") or ""
        supplied = request.headers.get("x-antmonitor-token", "")
        if not expected or not secrets.compare_digest(
            supplied.encode(), expected.encode()
        ):
            return error_response("AGENTLENS_SERVICE_UNAUTHORIZED", 4001, status=401)
        if not isinstance(request.data, dict):
            return error_response("INVALID_INGEST_SCOPE", 4220, status=422)
        space_uuid = request.data.get("space_uuid")
        agent_uuid = request.data.get("agent_uuid")
        if not all(
            isinstance(value, str) and value for value in (space_uuid, agent_uuid)
        ):
            return error_response("INVALID_INGEST_SCOPE", 4220, status=422)
        agent = (
            Agent.objects.select_related("space", "observation_subject_binding__user")
            .filter(
                uuid=agent_uuid,
                space__uuid=space_uuid,
                status__in=[1, 2],
                gmt_deleted__isnull=True,
                space__status=1,
                space__gmt_deleted__isnull=True,
            )
            .first()
        )
        if agent is None:
            return error_response("RESOURCE_NOT_IN_SPACE", 4004, status=404)
        try:
            subject = require_agent_subject(agent)
        except ObservationSubjectError:
            return error_response("SUBJECT_BINDING_MISSING", 4003, status=403)
        ttl = min(300, int(settings.AIVISION_INGEST_TOKEN_TTL_SECONDS))
        try:
            token, _ = build_ingest_token(
                space_uuid=agent.space.uuid,
                org_id=current_ingest_org_id(),
                subject_id=subject,
                key_id="agentlens",
                agent_uuid=agent.uuid,
                agent_product=agent.agent_product,
                ttl_seconds=ttl,
            )
        except IngestTokenError:
            return error_response("INGEST_SIGNING_UNAVAILABLE", 5000, status=503)
        response = success_response(
            {
                "token": token,
                "token_type": "Bearer",
                "expires_in": ttl,
                "space_id": agent.space.uuid,
                "agent_uuid": agent.uuid,
            }
        )
        response["Cache-Control"] = "no-store"
        return response


def _key_is_active(space_key):
    # Authentication caches keys. Installation and renewal must observe a
    # revocation immediately, even when a different worker handled deletion.
    return ingest_credential_is_active(space_key)


def _ingest_agents(space):
    return active_ingest_agents(space)


def _ttl(value):
    maximum = int(settings.AIVISION_INGEST_TOKEN_TTL_SECONDS)
    try:
        ttl = int(value if value is not None else maximum)
    except (TypeError, ValueError):
        return None
    return ttl if 0 < ttl <= maximum else None


def _mint(*, space, space_key, agent_uuid, ttl_value):
    if not _key_is_active(space_key):
        return error_response("SPACE_KEY_INVALID", 4001, status=401)
    if space.status != 1 or space.gmt_deleted is not None:
        return error_response(
            "SPACE_DISABLED",
            4003,
            status=403,
            data={"error_key": "SPACE_DISABLED"},
        )
    ttl = _ttl(ttl_value)
    if ttl is None:
        return error_response(
            "INVALID_TTL", 4000, status=422, data={"error_key": "INVALID_TTL"}
        )
    agent = _ingest_agents(space).filter(uuid=agent_uuid).first()
    if agent is None or (
        isinstance(space_key, AgentInstallation) and agent.pk != space_key.agent_id
    ):
        return error_response(
            "RESOURCE_NOT_IN_SPACE",
            4004,
            status=404,
            data={"error_key": "RESOURCE_NOT_IN_SPACE"},
        )
    binding = agent.observation_subject_binding
    if binding.space_id != space.id:
        return error_response(
            "INGEST_SCOPE_MISMATCH",
            4003,
            status=409,
            data={"error_key": "INGEST_SCOPE_MISMATCH"},
        )
    kid = (
        f"agent-installation:{space_key.uuid}"
        if isinstance(space_key, AgentInstallation)
        else space_key.key_alias or str(space_key.id)
    )
    try:
        token, exp = build_ingest_token(
            space_uuid=space.uuid,
            org_id=current_ingest_org_id(),
            subject_id=binding.subject_id,
            key_id=kid,
            agent_uuid=agent.uuid,
            agent_product=agent.agent_product,
            ttl_seconds=ttl,
        )
    except IngestTokenError:
        logger.exception("Ingest token signing failed")
        return error_response("INGEST_SIGNING_UNAVAILABLE", 5000, status=500)
    return success_response(
        {
            "token": token,
            "token_type": "Bearer",
            "expires_in": ttl,
            "expires_at": timezone.datetime.fromtimestamp(
                exp, tz=timezone.get_current_timezone()
            ).isoformat(),
            "endpoint": settings.AIVISION_OTLP_INGEST_ENDPOINT,
            "key_id": kid,
            "space_id": space.uuid,
            "agent_uuid": agent.uuid,
            "agent_product": agent.agent_product,
        }
    )


class SpaceKeyIngestTokenView(APIView):
    def post(self, request, space_id, key_id):
        ctx = require_space_permission(
            user=request.user,
            space_id=space_id,
            permission=AGENT_INGEST_TOKEN_MINT,
        )
        space_key = SpaceKey.objects.filter(
            id=key_id,
            space=ctx.space,
            enabled=True,
            gmt_deleted__isnull=True,
        ).first()
        if space_key is None:
            return error_response(
                "RESOURCE_NOT_IN_SPACE",
                4004,
                status=404,
                data={"error_key": "RESOURCE_NOT_IN_SPACE"},
            )
        return _mint(
            space=ctx.space,
            space_key=space_key,
            agent_uuid=request.data.get("agent_uuid"),
            ttl_value=request.data.get("ttl_seconds"),
        )


class SpaceKeyMintTokenView(APIView):
    authentication_classes = [AgentIngestAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        space_key = request.auth
        return _mint(
            space=space_key.space,
            space_key=space_key,
            agent_uuid=request.data.get("agent_uuid"),
            ttl_value=request.data.get("ttl_seconds"),
        )


class SpaceKeyAgentInstallationView(APIView):
    """Acknowledge native registration; token renewal never changes Agent status."""

    authentication_classes = [AgentIngestAuthentication]
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request, agent_uuid):
        if not _key_is_active(request.auth):
            return error_response("SPACE_KEY_INVALID", 4001, status=401)
        space = request.auth.space
        if (
            isinstance(request.auth, AgentInstallation)
            and str(agent_uuid) != request.auth.agent.uuid
        ):
            return error_response("RESOURCE_NOT_IN_SPACE", 4004, status=404)
        agent = market_agents(space).select_for_update().filter(uuid=agent_uuid).first()
        if agent is None:
            return error_response("RESOURCE_NOT_IN_SPACE", 4004, status=404)
        if agent.observation_subject_binding.space_id != space.id:
            return error_response("INGEST_SCOPE_MISMATCH", 4003, status=409)
        if agent.status == 2:
            agent.status = 1
            agent.save(update_fields=["status", "gmt_modified"])
        sync_personal_diagnosis_after_commit(agent)
        return success_response(
            {"agent_uuid": agent.uuid, "space_id": space.uuid, "status": agent.status}
        )
