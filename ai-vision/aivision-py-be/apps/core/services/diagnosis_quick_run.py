"""Authorize and orchestrate bounded quick runs via the platform HTTP gateway."""

from datetime import timedelta
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import Agent, DiagnosisQuickRunRequest
from apps.core.permissions.constants import (
    DIAGNOSIS_EXECUTE,
    MODEL_CONNECTION_MANAGE,
)
from apps.core.permissions.space import (
    require_space_permission,
)
from apps.core.services.observation_subject_service import (
    require_agent_subject,
)
from apps.core.trace_query.dataset_spec import build_default_dataset

from .diagnosis_authorization import authorize_diagnosis_agent, diagnosis_dataset_for
from .diagnosis_quick_input import (
    QuickRunError,
    digest,
    normalize_input,
    normalize_run_input,
)


def remote(method, path, **kwargs):
    try:
        response = requests.request(
            method,
            settings.AGENTLENS_BASE_URL.rstrip("/") + "/api/v1/" + path.lstrip("/"),
            headers={"x-antmonitor-token": settings.AGENTLENS_TOKEN},
            timeout=15,
            **kwargs,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise QuickRunError("QUICK_UPSTREAM_UNAVAILABLE", 503, True) from None
    if not response.ok:
        key = payload.get("msg", "") if isinstance(payload, dict) else ""
        body = kwargs.get("json")
        if (
            method == "POST"
            and path == "runs/options"
            and isinstance(body, dict)
            and body.get("ensure_core_presets") is True
            and response.status_code == 422
            and key == "ensure_core_presets: Extra inputs are not permitted"
            and payload.get("code") == "validation"
        ):
            # Older AgentLens rejected the optional flag before running the handler.
            kwargs["json"] = {
                name: value
                for name, value in body.items()
                if name != "ensure_core_presets"
            }
            return remote(method, path, **kwargs)
        allowed = {
            "DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE",
            "QUICK_INPUT_INVALID",
            "QUICK_MODEL_UNAVAILABLE",
            "QUICK_TRACE_SCOPE_REJECTED",
            "QUICK_BEFORE_UNSUPPORTED",
            "QUICK_PLAN_CHANGED",
            "QUICK_RESOURCE_EXPIRED",
            "QUICK_REQUEST_CONFLICT",
        }
        if key not in allowed:
            key = "QUICK_UPSTREAM_UNAVAILABLE"
        status = (
            410
            if key == "QUICK_RESOURCE_EXPIRED"
            else (
                409 if key in {"QUICK_PLAN_CHANGED", "QUICK_REQUEST_CONFLICT"} else 422
            )
        )
        if key == "QUICK_UPSTREAM_UNAVAILABLE":
            status = 503
        raise QuickRunError(key, status, status == 503)
    return payload


def authorize(user, space_id, agent_uuid, *, lock=False, permission=DIAGNOSIS_EXECUTE):
    return authorize_diagnosis_agent(
        user,
        space_id,
        agent_uuid,
        error_type=QuickRunError,
        prefix="QUICK",
        lock=lock,
        permission=permission,
        permission_reader=require_space_permission,
        agent_model=Agent,
        subject_reader=require_agent_subject,
    )


def dataset_for(ctx, agent):
    return diagnosis_dataset_for(
        ctx,
        agent,
        error_type=QuickRunError,
        prefix="QUICK",
        builder=build_default_dataset,
        subject_reader=require_agent_subject,
    )


def options(user, space_id, agent_uuid):
    ctx, agent = authorize(user, space_id, agent_uuid)
    result = {
        "can_execute": True,
        "denial_reason": None,
        "can_configure_models": MODEL_CONNECTION_MANAGE in ctx.permissions,
        "scenes": [],
        "templates": [],
        "model_connections": [],
        "limits": {"max_traces": 10, "max_llm_templates": 1},
    }
    try:
        dataset_for(ctx, agent)
    except QuickRunError as exc:
        result.update(can_execute=False, denial_reason=exc.key)
        return result
    result.update(remote("GET", "quick-runs/catalog"))
    models = remote("GET", "model-connections", params={"space_uuid": space_id})
    for model in models.get("items", []):
        if model.get("space_uuid") != space_id:
            raise QuickRunError("QUICK_MODEL_UNAVAILABLE")
        try:
            url = urlsplit(model.get("endpoint", ""))
            configured = bool(
                url.scheme in {"http", "https"}
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and model.get("model_name", "").strip()
                and model.get("api_key_configured")
            )
        except ValueError:
            configured = False
        result["model_connections"].append(
            {
                k: model.get(k)
                for k in ("uuid", "name", "model_name", "api_key_configured")
            }
            | {
                "configuration_complete": configured,
                "selectable": configured,
                "unavailable_reason": None if configured else "QUICK_MODEL_UNAVAILABLE",
                "last_probe_status": model["status"],
            }
        )
    return result


def run_options(user, space_id, agent_uuid):
    ctx, agent = authorize(user, space_id, agent_uuid)
    result = {
        "can_execute": True,
        "denial_reason": None,
        "diagnosers": [],
        "limits": {"max_traces": 10, "max_diagnosers": 1},
    }
    try:
        spec = dataset_for(ctx, agent)
    except QuickRunError as exc:
        result.update(can_execute=False, denial_reason=exc.key)
        return result
    result.update(
        remote(
            "POST",
            "runs/options",
            json={
                "space_uuid": str(ctx.space.uuid),
                "agent_uuid": str(agent.uuid),
                "dataset_spec": spec,
                **({"ensure_core_presets": True} if ctx.space.type == 1 else {}),
            },
        )
    )
    return result


def receipt_authorized(user, space_id, receipt):
    if (
        receipt is None
        or str(receipt.space.uuid) != str(space_id)
        or receipt.user_id != user.pk
        or receipt.gmt_deleted is not None
    ):
        raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
    ctx, agent = authorize(user, space_id, receipt.agent.uuid)
    return ctx, agent


def receipt_context(user, space_id, receipt):
    ctx, agent = receipt_authorized(user, space_id, receipt)
    context = remote("GET", f"quick-runs/tasks/{receipt.task_uuid}/context")
    expected = {
        "space_uuid": str(ctx.space.uuid),
        "agent_uuid": str(agent.uuid),
        "job_uuid": receipt.job_uuid,
        "dataset_uuid": receipt.dataset_uuid,
        "task_uuid": receipt.task_uuid,
    }
    if (
        any(str(context.get(k)) != v for k, v in expected.items())
        or context.get("trace_ids") != receipt.canonical_input["trace_ids"]
    ):
        raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
    return ctx, context


def submit(user, space_id, agent_uuid, body, *, saved_diagnoser=False):
    intent = (normalize_run_input if saved_diagnoser else normalize_input)(
        body, agent_uuid
    )
    remote_prefix = "runs" if saved_diagnoser else "quick-runs"
    ctx, agent = authorize(user, space_id, agent_uuid)
    spec = dataset_for(ctx, agent)
    existing = (
        DiagnosisQuickRunRequest.objects.select_related("space", "agent")
        .filter(space=ctx.space, user=user, request_id=intent["request_id"])
        .first()
    )
    fingerprint = digest(intent)
    if existing and existing.input_digest != fingerprint:
        raise QuickRunError("QUICK_REQUEST_CONFLICT", 409)
    before = intent.get("before_task_uuid")
    if before:
        original = (
            DiagnosisQuickRunRequest.objects.select_related("space", "agent")
            .filter(task_uuid=before)
            .first()
        )
        _, old_context = receipt_context(user, space_id, original)
        if original.agent_id != agent.pk or not old_context["can_retest"]:
            raise QuickRunError("QUICK_BEFORE_UNSUPPORTED")
    if existing:
        receipt_authorized(user, space_id, existing)
        # Validate frozen source/model policy before any receipt lease update.
        for field in (
            "space_filter",
            "username",
            "username_column",
            "agent_product_filter",
            "agent_product_column",
            "runtime_type",
        ):
            if existing.resolved_plan["dataset_spec"].get(field) != spec.get(field):
                raise QuickRunError("QUICK_PLAN_CHANGED", 409)
        if existing.resolved_plan["dataset_spec"].get("query", {}).get(
            "table"
        ) != spec.get("query", {}).get("table"):
            raise QuickRunError("QUICK_PLAN_CHANGED", 409)
        # The saved-reference path validates drift only if no owned Task exists.
        # A lost accepted response must remain recoverable after definition changes.
        if not saved_diagnoser:
            remote("POST", "quick-runs/validate", json={"plan": existing.resolved_plan})
        prepared = {"plan": existing.resolved_plan, "plan_digest": existing.plan_digest}
    else:
        payload = {k: v for k, v in intent.items() if k not in {"request_id"}}
        payload.update(space_uuid=str(ctx.space.uuid), dataset_spec=spec)
        prepared = remote("POST", f"{remote_prefix}/preflight", json=payload)
    now = timezone.now()
    with transaction.atomic():
        locked_ctx, locked_agent = authorize(user, space_id, agent_uuid, lock=True)
        locked_spec = dataset_for(locked_ctx, locked_agent)
        for field in (
            "space_filter",
            "username",
            "username_column",
            "agent_product_filter",
            "agent_product_column",
            "runtime_type",
        ):
            if locked_spec.get(field) != prepared["plan"]["dataset_spec"].get(field):
                raise QuickRunError("QUICK_PLAN_CHANGED", 409)
        if locked_spec.get("query", {}).get("table") != prepared["plan"][
            "dataset_spec"
        ].get("query", {}).get("table"):
            raise QuickRunError("QUICK_PLAN_CHANGED", 409)
        try:
            with transaction.atomic():
                receipt, _ = DiagnosisQuickRunRequest.objects.get_or_create(
                    space=ctx.space,
                    user=user,
                    request_id=intent["request_id"],
                    defaults={
                        "agent": agent,
                        "canonical_input": intent,
                        "input_digest": fingerprint,
                        "resolved_plan": prepared["plan"],
                        "plan_digest": prepared["plan_digest"],
                    },
                )
        except IntegrityError:
            receipt = DiagnosisQuickRunRequest.objects.get(
                space=ctx.space, user=user, request_id=intent["request_id"]
            )
        receipt = DiagnosisQuickRunRequest.objects.select_for_update().get(
            pk=receipt.pk
        )
        if receipt.input_digest != fingerprint:
            raise QuickRunError("QUICK_REQUEST_CONFLICT", 409)
        if receipt.lease_until and receipt.lease_until > now:
            return {
                "state": "provisioning",
                "request_id": intent["request_id"],
                "retry_after_seconds": 2,
            }
        receipt.lease_epoch += 1
        epoch = receipt.lease_epoch
        receipt.lease_until = now + timedelta(seconds=60)
        receipt.save(update_fields=["lease_epoch", "lease_until", "gmt_modified"])
    try:
        result = remote(
            "POST",
            remote_prefix,
            json={
                "receipt_uuid": receipt.uuid,
                "request_identity": digest(
                    [str(ctx.space.uuid), str(user.uuid), intent["request_id"]]
                ),
                "input_digest": receipt.input_digest,
                "plan_digest": receipt.plan_digest,
                "plan": receipt.resolved_plan,
                "dataset_uuid": receipt.dataset_uuid,
                "job_uuid": receipt.job_uuid,
                "task_uuid": receipt.task_uuid,
            },
        )
        if any(
            result.get(k) != getattr(receipt, k)
            for k in ("dataset_uuid", "job_uuid", "task_uuid")
        ):
            raise QuickRunError("QUICK_UPSTREAM_UNAVAILABLE", 503, True)
    except QuickRunError as exc:
        DiagnosisQuickRunRequest.objects.filter(
            pk=receipt.pk, lease_epoch=epoch
        ).update(lease_until=None, safe_error_code=exc.key)
        raise
    updated = DiagnosisQuickRunRequest.objects.filter(
        pk=receipt.pk, lease_epoch=epoch
    ).update(provisioning_step="accepted", lease_until=None, safe_error_code="")
    if not updated:
        return {
            "state": "provisioning",
            "request_id": intent["request_id"],
            "retry_after_seconds": 2,
        }
    authorize(user, space_id, agent_uuid)
    return {"state": "accepted", "request_id": intent["request_id"], **result}
