"""Shared read authorization and trusted dataset scope for diagnosis inputs."""

from apps.core.models import Agent
from apps.core.permissions.constants import DIAGNOSIS_EXECUTE
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    require_space_permission,
)
from apps.core.services.observation_subject_service import (
    ObservationSubjectError,
    require_agent_subject,
)
from apps.core.trace_query.dataset_spec import build_default_dataset


def authorize_diagnosis_agent(
    user,
    space_id,
    agent_uuid,
    *,
    error_type,
    prefix,
    lock=False,
    permission=DIAGNOSIS_EXECUTE,
    permission_reader=require_space_permission,
    agent_model=Agent,
    subject_reader=require_agent_subject,
):
    try:
        ctx = permission_reader(
            user=user, space_id=space_id, permission=permission, lock=lock
        )
    except SpaceAuthorizationError:
        raise error_type(prefix + "_PERMISSION_DENIED", 403) from None
    query = agent_model.objects.select_related(
        "agent_type", "observation_subject_binding__user"
    ).filter(
        uuid=agent_uuid,
        space=ctx.space,
        status__in=[1, 2],
        gmt_deleted__isnull=True,
        agent_type__isnull=False,
        agent_type__status=1,
        agent_type__gmt_deleted__isnull=True,
    )
    agent = (query.select_for_update() if lock else query).first()
    if agent is None:
        raise error_type(prefix + "_AGENT_UNAVAILABLE", 404)
    try:
        subject = subject_reader(agent)
    except ObservationSubjectError:
        raise error_type(prefix + "_AGENT_UNAVAILABLE", 404) from None
    if agent.observation_subject_binding.user_id != user.pk or not subject:
        raise error_type(prefix + "_PERMISSION_DENIED", 403)
    return ctx, agent


def diagnosis_dataset_for(
    ctx,
    agent,
    *,
    error_type,
    prefix,
    builder=build_default_dataset,
    subject_reader=require_agent_subject,
):
    try:
        result = builder(ctx.space, ctx.user, agent.agent_product, agent=agent)
    except Exception:
        raise error_type(prefix + "_DATASOURCE_UNAVAILABLE") from None
    if not result or not result["spec"].get("agent_product_filter"):
        raise error_type(prefix + "_DATASOURCE_UNAVAILABLE")
    result["spec"].update(scope_type="personal", username=subject_reader(agent))
    result["spec"].setdefault("username_column", "user_id")
    return result["spec"]
