"""Bounded, typed progress reporting without host execution or provider secrets."""

import copy
import hashlib
import posixpath
import re
import secrets
import unicodedata
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import SandboxInstallationSession, Space
from apps.core.permissions.constants import SANDBOX_CONNECTION_MANAGE
from apps.core.permissions.space import require_space_permission

STAGES = ("configuration", "docker", "image", "state", "container", "readiness")
BOOTSTRAP_STAGES = (
    "host",
    "images",
    "configuration",
    "gateway",
    "plugin",
    "readiness",
    "registration",
)
TERMINAL = frozenset({"ready", "not_ready", "failed", "revoked", "expired"})
TOKEN_PREFIX = "aiv-sandbox-report-"
MAX_REQUEST_BYTES = 16384
MAX_ACTIVE_SESSIONS = 10
IMAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}\Z")
RUN_PATTERN = re.compile(r"[a-f0-9]{32}\Z")
LAYER_PATTERN = re.compile(r"[a-f0-9]{12,64}\Z")
QUANTITY_PATTERN = re.compile(
    r"[0-9]{1,12}(?:\.[0-9]{1,3})?(?:B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)\Z"
)
IMAGE_STATUSES = frozenset(
    {
        "Pulling fs layer",
        "Waiting",
        "Downloading",
        "Verifying Checksum",
        "Download complete",
        "Extracting",
        "Pull complete",
        "Already exists",
    }
)
CONFIG_FIELDS = frozenset(
    {
        "label",
        "image",
        "gateway_endpoint",
        "workload_image",
        "config_dir",
        "docker_socket",
        "docker_socket_gid",
        "port",
        "workspace",
        "timeout_seconds",
        "gateway_token_file",
        "gateway_ca_file",
        "gateway_cert_file",
        "gateway_key_file",
    }
)
PATH_FIELDS = (
    "config_dir",
    "docker_socket",
    "gateway_token_file",
    "gateway_ca_file",
    "gateway_cert_file",
    "gateway_key_file",
)


class InstallationError(Exception):
    def __init__(self, code="SANDBOX_INSTALLATION_INVALID", status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _text(value, limit, *, empty=False):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and (empty or bool(value))
        and not any(unicodedata.category(char) == "Cc" for char in value)
    )


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def validate_configuration(raw):
    if not isinstance(raw, dict) or set(raw) - CONFIG_FIELDS:
        raise InstallationError()
    config = {
        "label": "OpenShell",
        "docker_socket": "/var/run/docker.sock",
        "docker_socket_gid": None,
        "port": 8092,
        "workspace": "default",
        "timeout_seconds": 900,
        **raw,
    }
    if any(
        key not in config
        for key in ("image", "gateway_endpoint", "workload_image", "config_dir")
    ):
        raise InstallationError()
    if not _text(config["label"], 128) or not _text(config["workspace"], 128):
        raise InstallationError()
    if not all(
        isinstance(config[key], str) and IMAGE_PATTERN.fullmatch(config[key])
        for key in ("image", "workload_image")
    ):
        raise InstallationError()
    gateway = config["gateway_endpoint"]
    if not _text(gateway, 512) or any(char in gateway for char in "/@?# \t"):
        raise InstallationError()
    match = re.fullmatch(r"(?:\[[^\[\]\s]+\]|[^:\s]+):([0-9]+)", gateway)
    if match is None or not 1 <= int(match[1]) <= 65535:
        raise InstallationError()
    for key in PATH_FIELDS:
        path = config.get(key)
        if key not in config:
            continue
        if path == "" and key not in {"config_dir", "docker_socket"}:
            config.pop(key)
            continue
        if (
            not _text(path, 1024)
            or not path.startswith("/")
            or "," in path
            or posixpath.normpath(path).strip("/") == ""
        ):
            raise InstallationError()
    if bool(config.get("gateway_cert_file")) != bool(config.get("gateway_key_file")):
        raise InstallationError()
    if (
        not _integer(config["port"], 1, 65535)
        or not _integer(config["timeout_seconds"], 60, 1800)
        or (
            config["docker_socket_gid"] is not None
            and not _integer(config["docker_socket_gid"], 0, 2147483647)
        )
    ):
        raise InstallationError()
    return config


def snapshot(session):
    status = session.status
    if status not in TERMINAL and session.expires_at <= timezone.now():
        status = "expired"
    return {
        "uuid": session.uuid,
        "space_uuid": session.space.uuid,
        "provider": "openshell",
        "operation": session.operation,
        "mode": session.mode,
        "bootstrap_plan_id": (session.bootstrap_plan or {}).get("plan_id"),
        "registration_state": session.registration_state,
        "connection_uuid": session.connection_uuid,
        "configuration": session.configuration,
        "status": status,
        "created_at": session.gmt_create.isoformat(),
        "updated_at": session.gmt_modified.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "last_received_at": (
            session.last_received_at.isoformat() if session.last_received_at else None
        ),
        "last_seq": session.last_seq,
        "event_count": session.event_count,
        "stages": session.stages,
        "events": session.events,
    }


@transaction.atomic
def create_installation(*, user, space_id, body):
    bootstrap = isinstance(body, dict) and body == {"operation": "bootstrap"}
    plan = None
    if bootstrap:
        from .sandbox_bootstrap import bootstrap_plan

        plan = bootstrap_plan()
    if (
        not isinstance(body, dict)
        or (not bootstrap and set(body) != {"operation", "configuration"})
        or not isinstance(body["operation"], str)
        or body["operation"] not in {"install", "status", "bootstrap"}
        or (body["operation"] == "bootstrap" and not bootstrap)
    ):
        raise InstallationError()
    config = (
        {"label": "OpenShell", "image": "", "workload_image": ""}
        if bootstrap
        else validate_configuration(body["configuration"])
    )
    # Serialize the per-Space cap with other creates; GET never creates receipts.
    Space.objects.select_for_update().get(uuid=space_id)
    ctx = require_space_permission(
        user=user, space_id=space_id, permission=SANDBOX_CONNECTION_MANAGE, lock=True
    )
    now = timezone.now()
    owned = SandboxInstallationSession.objects.filter(space=ctx.space, creator=user)
    owned.filter(gmt_create__lt=now - timedelta(days=30)).delete()
    if (
        owned.filter(
            status__in=("waiting", "running"),
            expires_at__gt=now,
            revoked_at__isnull=True,
        ).count()
        >= MAX_ACTIVE_SESSIONS
    ):
        raise InstallationError("SANDBOX_INSTALLATION_LIMIT", 409)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    session = SandboxInstallationSession.objects.create(
        space=ctx.space,
        creator=user,
        operation=body["operation"],
        mode="bootstrap" if bootstrap else "manual",
        bootstrap_plan=plan,
        configuration=config,
        report_token_hash=digest(raw),
        expires_at=now + timedelta(minutes=60),
        stages=[
            {"stage": stage, "state": "pending", "detail": "", "elapsed_ms": 0}
            for stage in (
                BOOTSTRAP_STAGES
                if bootstrap
                else (STAGES if body["operation"] == "install" else ("readiness",))
            )
        ],
    )
    result = {
        "session": snapshot(session),
        "report_token": raw,
        "report_path": f"/api/public/sandbox-installations/{session.uuid}/events",
    }
    if bootstrap:
        bootstrap_token = "aiv-sandbox-bootstrap-" + secrets.token_urlsafe(32)
        enrollment_token = "aiv-sandbox-enroll-" + secrets.token_urlsafe(32)
        session.bootstrap_token_hash = digest(bootstrap_token)
        session.enrollment_token_hash = digest(enrollment_token)
        session.save(update_fields=["bootstrap_token_hash", "enrollment_token_hash"])
        result.update(
            bootstrap_token=bootstrap_token,
            enrollment_token=enrollment_token,
            bootstrap_path=f"/api/public/sandbox-installations/{session.uuid}",
        )
    return result


DIAGNOSTIC_CODES = {
    "docker_version_unsupported",
    "landlock_unavailable",
    "landlock_abi_unsupported",
    "environment_probe_failed",
    "image_download_failed",
    "gateway_start_failed",
    "plugin_start_failed",
    "provider_not_ready",
    "registration_failed",
    "configuration_failed",
    "timeout",
    "canceled",
    "docker_unavailable",
    "docker_permission_denied",
    "docker_context_unsupported",
    "deployment_not_found",
    "deployment_ambiguous",
}
DIAGNOSTIC_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)*\Z")


def _validate_diagnostic(raw, event, operation):
    if (
        operation != "bootstrap"
        or not isinstance(raw, dict)
        or not {"code"} <= raw.keys()
        or raw.keys() - {"code", "actual", "required"}
        or not isinstance(raw.get("code"), str)
        or raw["code"] not in DIAGNOSTIC_CODES
        or not (
            event["state"] == "failed"
            or (
                event["stage"] == "readiness"
                and event["state"] == "done"
                and event["detail"] == "not_ready"
                and raw["code"] == "provider_not_ready"
            )
        )
    ):
        raise InstallationError()
    for key in ("actual", "required"):
        if key in raw and (
            not isinstance(raw[key], str)
            or not 1 <= len(raw[key]) <= 64
            or not DIAGNOSTIC_NUMBER.fullmatch(raw[key])
        ):
            raise InstallationError()
    return copy.deepcopy(raw)


def _validate_event(raw, operation):
    stages = BOOTSTRAP_STAGES if operation == "bootstrap" else STAGES
    required = {"seq", "stage", "state", "detail", "elapsed_ms"}
    if (
        not isinstance(raw, dict)
        or not required <= set(raw)
        or set(raw) - (required | {"image", "diagnostic"})
    ):
        raise InstallationError()
    if (
        not _integer(raw["seq"], 1, 2147483647)
        or not _integer(raw["elapsed_ms"], 0, 3600000)
        or not isinstance(raw["stage"], str)
        or raw["stage"] not in stages
        or not isinstance(raw["state"], str)
        or raw["state"] not in {"start", "wait", "done", "failed"}
        or (operation == "status" and raw["stage"] != "readiness")
    ):
        raise InstallationError()
    detail = raw["detail"]
    allowed = {""}
    if raw["state"] == "failed":
        allowed |= {"timeout", "canceled"}
        if operation == "status":
            allowed.add("not_installed")
    elif raw["stage"] in {"image", "images"}:
        allowed |= (
            {"pulling"}
            if raw["state"] == "wait"
            else ({"cached", "pulled"} if raw["state"] == "done" else set())
        )
    elif raw["stage"] in {"readiness", "registration"} and raw["state"] == "done":
        allowed = {"ready", "not_ready"}
    if not isinstance(detail, str) or detail not in allowed:
        raise InstallationError()
    if "image" in raw:
        image = raw["image"]
        if (
            raw["stage"] not in {"image", "images"}
            or raw["state"] != "wait"
            or not isinstance(image, dict)
            or set(image) != {"layer", "status", "current", "total"}
            or not isinstance(image["layer"], str)
            or not LAYER_PATTERN.fullmatch(image["layer"])
            or not isinstance(image["status"], str)
            or image["status"] not in IMAGE_STATUSES
            or not all(isinstance(image[key], str) for key in ("current", "total"))
        ):
            raise InstallationError()
        if bool(image["current"]) != bool(image["total"]):
            raise InstallationError()
        if image["current"] and (
            image["status"] not in {"Downloading", "Extracting"}
            or not QUANTITY_PATTERN.fullmatch(image["current"])
            or not QUANTITY_PATTERN.fullmatch(image["total"])
        ):
            raise InstallationError()
    if "diagnostic" in raw:
        _validate_diagnostic(raw["diagnostic"], raw, operation)
    return copy.deepcopy(raw)


@transaction.atomic
def receive_events(*, session_id, token_hash, body):
    session = (
        SandboxInstallationSession.objects.select_for_update()
        .select_related("space", "creator")
        .get(pk=session_id)
    )
    now = timezone.now()
    if (
        session.report_token_hash != token_hash
        or session.revoked_at
        or session.expires_at <= now
    ):
        raise InstallationError("SANDBOX_REPORT_UNAUTHORIZED", 401)
    require_space_permission(
        user=session.creator,
        space_id=session.space.uuid,
        permission=SANDBOX_CONNECTION_MANAGE,
        lock=True,
    )
    if (
        not isinstance(body, dict)
        or set(body) != {"operation", "run_id", "events"}
        or not isinstance(body["run_id"], str)
        or not RUN_PATTERN.fullmatch(body["run_id"])
        or not isinstance(body["events"], list)
        or not 1 <= len(body["events"]) <= 32
    ):
        raise InstallationError()
    if (
        body["operation"] != session.operation
        or (session.run_id and body["run_id"] != session.run_id)
        or (session.mode == "bootstrap" and not session.run_id)
    ):
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    events = [_validate_event(item, session.operation) for item in body["events"]]
    stages = copy.deepcopy(session.stages)
    history = copy.deepcopy(session.events)
    last_seq, status, count = session.last_seq, session.status, session.event_count
    for item in events:
        if item["seq"] <= last_seq:
            continue
        if item["seq"] != last_seq + 1 or status in TERMINAL:
            raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
        index = next(
            index
            for index, stage in enumerate(stages)
            if stage["stage"] == item["stage"]
        )
        stage = stages[index]
        if (
            any(previous["state"] != "done" for previous in stages[:index])
            or any(later["state"] != "pending" for later in stages[index + 1 :])
            or (item["state"] == "start" and stage["state"] != "pending")
            or (item["state"] != "start" and stage["state"] not in {"start", "wait"})
            or item["elapsed_ms"] < stage["elapsed_ms"]
        ):
            raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
        stage.update({key: item[key] for key in ("state", "detail", "elapsed_ms")})
        if "diagnostic" in item:
            stage["diagnostic"] = item["diagnostic"]
        status = "running"
        if item["state"] == "failed":
            status = "failed"
        elif (
            item["stage"] == "readiness"
            and item["state"] == "done"
            and session.mode != "bootstrap"
        ):
            status = item["detail"]
        elif (
            session.mode == "bootstrap"
            and item["stage"] == "readiness"
            and item["state"] == "done"
            and item["detail"] == "not_ready"
        ):
            status = "not_ready"
        elif item["stage"] == "registration" and item["state"] == "done":
            if item["detail"] != "ready" or session.registration_state != "saved":
                raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
            status = "ready"
        history.append({**item, "received_at": now.isoformat()})
        last_seq, count = item["seq"], count + 1
    if last_seq != session.last_seq:
        session.run_id = body["run_id"]
        session.stages, session.events = stages, history[-80:]
        session.last_seq, session.event_count = last_seq, count
        session.status, session.last_received_at = status, now
        session.save(
            update_fields=[
                "run_id",
                "stages",
                "events",
                "last_seq",
                "event_count",
                "status",
                "last_received_at",
                "gmt_modified",
            ]
        )
    return {"last_seq": last_seq}
