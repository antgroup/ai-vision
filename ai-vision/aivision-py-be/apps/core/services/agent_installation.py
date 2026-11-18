"""Authorization shared by browser installation and machine token renewal."""

from apps.core.models import Agent, AgentInstallation
from apps.core.permissions.constants import AGENT_MANAGE
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    require_space_permission,
)


INSTALL_TOKEN_PREFIX = "aiv-install-"
AGENT_CREDENTIAL_PREFIX = "aiv-agent-"


def active_ingest_agents(space):
    return Agent.objects.select_related("observation_subject_binding").filter(
        space=space,
        space__status=1,
        space__gmt_deleted__isnull=True,
        status__in=[1, 2],
        gmt_deleted__isnull=True,
        observation_subject_binding__status=1,
        observation_subject_binding__gmt_deleted__isnull=True,
        observation_subject_binding__user__status=1,
        observation_subject_binding__user__gmt_deleted__isnull=True,
    )


def market_agents(space):
    return active_ingest_agents(space).filter(
        agent_type__setup_mode="CLI",
        agent_type__category="CUSTOM",
        agent_type__status=1,
        agent_type__gmt_deleted__isnull=True,
    )


def installation_is_authorized(installation):
    if not installation.enabled:
        return False
    try:
        require_space_permission(
            user=installation.creator,
            space_id=installation.space.uuid,
            permission=AGENT_MANAGE,
        )
    except SpaceAuthorizationError:
        return False
    return market_agents(installation.space).filter(pk=installation.agent_id).exists()


def ingest_credential_is_active(credential):
    if isinstance(credential, AgentInstallation):
        return (
            credential.exchanged_at is not None
            and AgentInstallation.objects.filter(
                pk=credential.pk, enabled=True
            ).exists()
            and installation_is_authorized(credential)
        )
    from apps.core.models import SpaceKey

    return SpaceKey.objects.filter(
        pk=credential.pk,
        key_hash=credential.key_hash,
        enabled=True,
        gmt_deleted__isnull=True,
    ).exists()
