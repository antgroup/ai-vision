"""Explicit Specialist gateway; authorization precedes every upstream request."""

import requests
from django.conf import settings

from apps.core.permissions.constants import DIAGNOSIS_EXECUTE, DIAGNOSIS_MANAGE
from apps.core.services.observation_subject_service import ObservationSubjectError

from .diagnosis_authorization import authorize_diagnosis_agent, diagnosis_dataset_for
from .diagnosis_specialist_input import (
    DIAGNOSER_ID,
    ID,
    SpecialistError,
    normalize_configuration,
    normalize_sync,
    normalize_trial,
    text,
)
from .diagnosis_trial_timeout import LLM_TRIAL_MAX_FORWARD_TIMEOUT

ALLOWED = {
    ("GET", "catalog"): "diagnosers/specialist-catalog",
    ("POST", "sync"): "diagnosers/sync-specialist-builtins",
    ("POST", "trial"): "diagnoser/specialist-trial",
}
SAFE_ERRORS = {
    "SPECIALIST_INPUT_INVALID": 422,
    "SPECIALIST_PERMISSION_DENIED": 403,
    "SPECIALIST_AGENT_UNAVAILABLE": 404,
    "SPECIALIST_DATASOURCE_UNAVAILABLE": 422,
    "SPECIALIST_TRACE_SCOPE_REJECTED": 422,
    "SPECIALIST_UNAVAILABLE": 422,
    "SPECIALIST_CONFIGURATION_CONFLICT": 409,
    "SPECIALIST_MODEL_UNAVAILABLE": 422,
}


def remote(method, operation, *, uuid=None, **kwargs):
    if method == "PATCH" and operation == "configuration":
        text(uuid, 32, pattern=DIAGNOSER_ID)
        path = f"diagnosers/{uuid}/specialist-configuration"
    else:
        path = ALLOWED.get((method, operation))
        if path is None:
            raise SpecialistError()
    try:
        response = requests.request(
            method,
            settings.AGENTLENS_BASE_URL.rstrip("/") + "/api/v1/" + path,
            headers={"x-antmonitor-token": settings.AGENTLENS_TOKEN},
            timeout=LLM_TRIAL_MAX_FORWARD_TIMEOUT if operation == "trial" else 15,
            **kwargs,
        )
        payload = response.json()
    except requests.Timeout:
        if operation == "trial":
            raise SpecialistError("SPECIALIST_UPSTREAM_TIMEOUT", 504) from None
        raise SpecialistError("SPECIALIST_UPSTREAM_UNAVAILABLE", 503) from None
    except (requests.RequestException, ValueError):
        raise SpecialistError("SPECIALIST_UPSTREAM_UNAVAILABLE", 503) from None
    if not response.ok:
        key = payload.get("msg") if type(payload) is dict else None
        if type(key) is not str or key not in SAFE_ERRORS:
            raise SpecialistError("SPECIALIST_UPSTREAM_UNAVAILABLE", 503)
        raise SpecialistError(key, SAFE_ERRORS[key])
    if type(payload) is not dict:
        raise SpecialistError("SPECIALIST_UPSTREAM_UNAVAILABLE", 503)
    return payload


def authorize(user, space_id, agent_uuid, permission=DIAGNOSIS_EXECUTE):
    text(agent_uuid, 64, pattern=ID)
    return authorize_diagnosis_agent(
        user,
        space_id,
        agent_uuid,
        error_type=SpecialistError,
        prefix="SPECIALIST",
        permission=permission,
    )


def scope(ctx, agent):
    return {"space_uuid": str(ctx.space.uuid), "agent_uuid": str(agent.uuid)}


def dataset_for(ctx, agent):
    try:
        return diagnosis_dataset_for(
            ctx, agent, error_type=SpecialistError, prefix="SPECIALIST"
        )
    except ObservationSubjectError:
        raise SpecialistError("SPECIALIST_AGENT_UNAVAILABLE", 404) from None


def catalog(user, space_id, agent_uuid):
    ctx, agent = authorize(user, space_id, agent_uuid)
    return {
        **runtime_catalog(ctx, agent),
        "can_configure": ctx.space.type == 2 and DIAGNOSIS_MANAGE in ctx.permissions,
        "can_trial_draft": DIAGNOSIS_MANAGE in ctx.permissions,
    }


def runtime_catalog(ctx, agent):
    dataset = dataset_for(ctx, agent)
    return remote(
        "GET",
        "catalog",
        params={**scope(ctx, agent), "runtime_type": dataset.get("runtime_type")},
    )


def require_tool_specialist_runtime(ctx, agent):
    items = runtime_catalog(ctx, agent).get("items", [])
    if not any(
        item.get("key") == "required_tool_execution" and item.get("selectable") is True
        for item in items
    ):
        raise SpecialistError("SPECIALIST_UNAVAILABLE")


def sync(user, space_id, agent_uuid, body):
    ctx, agent = authorize(user, space_id, agent_uuid, DIAGNOSIS_MANAGE)
    if ctx.space.type != 2:
        raise SpecialistError("SPECIALIST_PERMISSION_DENIED", 403)
    value = normalize_sync(body)
    if value["key"] == "required_tool_execution":
        require_tool_specialist_runtime(ctx, agent)
    return remote(
        "POST", "sync", json={**value, **scope(ctx, agent), "operator": str(user.uuid)}
    )


def configure(user, space_id, agent_uuid, uuid, body):
    ctx, agent = authorize(user, space_id, agent_uuid, DIAGNOSIS_MANAGE)
    if ctx.space.type != 2:
        raise SpecialistError("SPECIALIST_PERMISSION_DENIED", 403)
    text(uuid, 32, pattern=DIAGNOSER_ID)
    value = normalize_configuration(body)
    if value["configuration"]["policy"]["kind"] == "required_tool_execution_v1":
        require_tool_specialist_runtime(ctx, agent)
    return remote(
        "PATCH",
        "configuration",
        uuid=uuid,
        json={**value, **scope(ctx, agent), "operator": str(user.uuid)},
    )


def trial(user, space_id, agent_uuid, body):
    ctx, agent = authorize(user, space_id, agent_uuid)
    value = normalize_trial(body)
    if "draft" in value:
        ctx, agent = authorize(user, space_id, agent_uuid, DIAGNOSIS_MANAGE)
    dataset = dataset_for(ctx, agent)
    return remote(
        "POST", "trial", json={**value, **scope(ctx, agent), "dataset_spec": dataset}
    )
