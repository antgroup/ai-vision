"""Browser-to-terminal onboarding without a manually created SpaceKey."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.models import Agent, AgentInstallation
from apps.core.permissions.constants import AGENT_MANAGE
from apps.core.permissions.space import require_space_permission
from apps.core.services.agent_installation import (
    AGENT_CREDENTIAL_PREFIX,
    INSTALL_TOKEN_PREFIX,
    installation_is_authorized,
    market_agents,
)
from apps.core.services.plugin_artifacts import public_base_url

from .common import error_response, success_response
from .ingest_tokens import _mint


def private_response(data):
    response = success_response(data)
    response["Cache-Control"] = "no-store"
    return response


class AgentInstallationTokenView(APIView):
    @transaction.atomic
    def post(self, request, space_id, agent_uuid):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_MANAGE
        )
        agent = (
            market_agents(ctx.space).select_for_update().filter(uuid=agent_uuid).first()
        )
        if agent is None:
            return error_response("Agent not found", 4004, status=404)
        raw, digest = AgentInstallation.generate_secret(INSTALL_TOKEN_PREFIX)
        expires = timezone.now() + timedelta(minutes=15)
        # Re-generating a command supersedes this user's unused command only;
        # installed machines and other users' installations keep working.
        AgentInstallation.objects.filter(
            agent=agent, creator=request.user, exchanged_at__isnull=True, enabled=True
        ).update(enabled=False)
        AgentInstallation.objects.create(
            agent=agent, creator=request.user, token_hash=digest, expires_at=expires
        )
        return private_response(
            {
                "agent_uuid": agent.uuid,
                "space_id": ctx.space.uuid,
                "installation_token": raw,
                "expires_at": expires.isoformat(),
                # Canonical onboarding origin — the SAME value catalog.json uses
                # for its artifact URLs (cdn_base / public_base_url). The install
                # card interpolates {baseUrl} with this instead of the browser's
                # window.location.origin, so the command's --base-url always
                # matches the origin the installer's same-origin download check
                # compares against, no matter which port serves the UI.
                "base_url": public_base_url(request),
            }
        )


class AgentInstallationExchangeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        raw = header[7:].strip() if header.startswith("Bearer ") else ""
        if not raw.startswith(INSTALL_TOKEN_PREFIX):
            return error_response("INSTALLATION_TOKEN_INVALID", 4001, status=401)
        # Lock the Agent before the grant to serialize against removal/reissue.
        grant = AgentInstallation.objects.filter(
            token_hash=AgentInstallation.digest(raw)
        ).first()
        if grant is None:
            return error_response("INSTALLATION_TOKEN_INVALID", 4001, status=401)
        Agent.objects.select_for_update().get(pk=grant.agent_id)
        grant = (
            AgentInstallation.objects.select_for_update()
            .select_related("agent__space", "creator")
            .get(pk=grant.pk)
        )
        if not installation_is_authorized(grant):
            return error_response("INSTALLATION_TOKEN_INVALID", 4001, status=401)
        if grant.exchanged_at or grant.expires_at <= timezone.now():
            return error_response(
                "INSTALLATION_TOKEN_EXPIRED_OR_USED", 4001, status=401
            )
        if (
            str(request.data.get("agent_uuid")) != grant.agent.uuid
            or str(request.data.get("space_id")) != grant.space.uuid
        ):
            return error_response("INSTALLATION_SCOPE_MISMATCH", 4003, status=403)
        credential, digest = AgentInstallation.generate_secret(AGENT_CREDENTIAL_PREFIX)
        grant.credential_hash = digest
        grant.exchanged_at = timezone.now()
        grant.save(update_fields=["credential_hash", "exchanged_at", "gmt_modified"])
        response = _mint(
            space=grant.space,
            space_key=grant,
            agent_uuid=grant.agent.uuid,
            ttl_value=None,
        )
        if response.status_code != 200:
            transaction.set_rollback(True)
            return response
        response.data["data"]["agent_credential"] = credential
        response["Cache-Control"] = "no-store"
        return response
