"""Immutable deployment plans and limited host enrollment, without host execution."""

import base64
import hashlib
import json
import os
import re
import socket
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.core.models import SandboxInstallationSession
from apps.core.permissions.constants import SANDBOX_CONNECTION_MANAGE
from apps.core.permissions.space import require_space_permission
from .sandbox_installation import (
    BOOTSTRAP_STAGES,
    InstallationError,
    RUN_PATTERN,
    digest,
)

_DIGEST_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64}\Z")
_ROLES = {"plugin", "worker", "gateway", "runtime", "supervisor"}
_PLATFORMS = {"linux/arm64", "linux/amd64"}


def _catalog():
    raw = os.environ.get("AIVISION_SANDBOX_BOOTSTRAP_CATALOG", "")
    try:
        if raw:
            if len(raw.encode()) > 65536:
                raise ValueError()
            catalog = json.loads(raw)
        else:
            encoded = os.environ.get("AIVISION_RELEASE_MANIFEST_BASE64", "")
            if len(encoded) > 350000:
                raise ValueError()
            manifest = json.loads(base64.b64decode(encoded, validate=True))
            catalog = manifest["plugins"]["openshell"]
        if (
            not isinstance(catalog, dict)
            or set(catalog)
            != {
                "schema_version",
                "product_version",
                "provider_version",
                "protocol_version",
                "images",
            }
            or type(catalog["schema_version"]) is not int
            or catalog["schema_version"] != 1
            or type(catalog["protocol_version"]) is not int
            or catalog["protocol_version"] != 1
            or catalog["provider_version"] != "0.1.6"
            or not isinstance(catalog["product_version"], str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", catalog["product_version"]
            )
            or not isinstance(catalog["images"], dict)
            or not catalog["images"]
            or set(catalog["images"]) - _PLATFORMS
        ):
            raise ValueError()
        for images in catalog["images"].values():
            if not isinstance(images, dict) or set(images) != _ROLES:
                raise ValueError()
            if any(
                not isinstance(ref, str)
                or len(ref) > 512
                or not _DIGEST_IMAGE.fullmatch(ref)
                for ref in images.values()
            ):
                raise ValueError()
        return catalog
    except (ValueError, TypeError, KeyError, RecursionError):
        raise InstallationError("catalog_unavailable", 409) from None


def bootstrap_plan():
    catalog = _catalog()
    hostname = os.environ.get("HOSTNAME") or socket.gethostname()
    diagnosis_url = settings.AGENTLENS_BASE_URL.rstrip("/")
    try:
        url = urlsplit(diagnosis_url)
        if (
            not re.fullmatch(r"[a-f0-9]{12,64}", hostname)
            or len(diagnosis_url) > 1024
            or not diagnosis_url.isascii()
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path
            or any(ord(char) < 33 or ord(char) == 127 for char in diagnosis_url)
        ):
            raise ValueError()
        _ = url.port
    except (ValueError, TypeError):
        raise InstallationError("deployment_unavailable", 409) from None
    key = hashlib.sha256(hostname.encode()).hexdigest()[:16]
    service = "aiv-openshell-" + key
    target = {
        "backend_hostname": hostname,
        "diagnosis_url": diagnosis_url,
        "deployment_key": key,
        "service_name": service,
        "gateway_name": service + "-gateway",
        "endpoint": f"http://{service}:8091",
    }
    canonical = json.dumps(
        {"catalog": catalog, "target": target}, sort_keys=True, separators=(",", ":")
    )
    return {
        "schema_version": 1,
        "plan_id": digest(canonical),
        "catalog": catalog,
        "target": target,
    }


def capability():
    result = {
        "available": False,
        "reason": None,
        "target_label": "AI Vision deployment host",
        "product_version": None,
        "provider_version": None,
        "stages": list(BOOTSTRAP_STAGES),
    }
    try:
        plan = bootstrap_plan()
        result.update(
            available=True,
            product_version=plan["catalog"]["product_version"],
            provider_version="0.1.6",
        )
    except InstallationError as exc:
        result["reason"] = exc.code
    return result


def _locked(session_id, token_field=None, token_hash=None):
    session = (
        SandboxInstallationSession.objects.select_for_update()
        .select_related("creator", "space")
        .get(pk=session_id)
    )
    if (
        session.mode != "bootstrap"
        or session.revoked_at
        or (
            token_field
            and (
                session.expires_at <= timezone.now()
                or getattr(session, token_field) != token_hash
            )
        )
    ):
        raise InstallationError("SANDBOX_REPORT_UNAUTHORIZED", 401)
    require_space_permission(
        user=session.creator,
        space_id=session.space.uuid,
        permission=SANDBOX_CONNECTION_MANAGE,
        lock=True,
    )
    return session


@transaction.atomic
def claim(*, session_id, token_hash, body):
    session = _locked(session_id, "bootstrap_token_hash", token_hash)
    if (
        not isinstance(body, dict)
        or set(body) != {"run_id"}
        or not isinstance(body["run_id"], str)
        or not RUN_PATTERN.fullmatch(body["run_id"])
    ):
        raise InstallationError()
    if session.run_id and session.run_id != body["run_id"]:
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    if not session.run_id:
        session.run_id = body["run_id"]
        session.save(update_fields=["run_id"])
    return {"plan": session.bootstrap_plan}


def connection_uuid(space_uuid, deployment_key):
    return uuid5(NAMESPACE_URL, f"aivision:sandbox:{space_uuid}:{deployment_key}").hex


def enrollment_remote(method, path, *, body, base_url=None):
    # Never forward an upstream body, URL, exception or credential as an error.
    try:
        response = requests.request(
            method,
            (base_url or settings.AGENTLENS_BASE_URL).rstrip("/") + path,
            headers={
                "x-antmonitor-token": settings.AGENTLENS_TOKEN,
                "Content-Type": "application/json",
            },
            **({"params": body} if method.upper() == "GET" else {"json": body}),
            timeout=30,
            allow_redirects=False,
        )
        if not response.ok:
            raise InstallationError(
                "SANDBOX_ENROLLMENT_UNAVAILABLE",
                (
                    response.status_code
                    if response.status_code in {404, 409, 422}
                    else 502
                ),
            )
        if len(response.content) > 65536:
            raise InstallationError("SANDBOX_ENROLLMENT_UNAVAILABLE", 502)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (requests.RequestException, ValueError):
        raise InstallationError("SANDBOX_ENROLLMENT_UNAVAILABLE", 502) from None


@transaction.atomic
def enroll(*, session_id, token_hash, body):
    session = _locked(session_id, "enrollment_token_hash", token_hash)
    if (
        not isinstance(body, dict)
        or set(body) != {"run_id", "plan_id", "platform", "api_key"}
        or not isinstance(body["run_id"], str)
        or not RUN_PATTERN.fullmatch(body["run_id"])
        or not isinstance(body["plan_id"], str)
        or not isinstance(body["platform"], str)
        or not isinstance(body["api_key"], str)
        or not 1 <= len(body["api_key"]) <= 8192
        or any(not 33 <= ord(char) <= 126 for char in body["api_key"])
    ):
        raise InstallationError()
    plan = session.bootstrap_plan
    if (
        not session.run_id
        or body["run_id"] != session.run_id
        or body["plan_id"] != plan["plan_id"]
        or body["platform"] not in plan["catalog"]["images"]
        or session.status in {"failed", "not_ready", "revoked", "expired"}
        or not any(
            stage["stage"] == "readiness"
            and stage["state"] == "done"
            and stage["detail"] == "ready"
            for stage in session.stages
        )
    ):
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    api_key = body["api_key"]
    key_hash = salted_hmac("aivision.sandbox.enrollment", api_key, algorithm="sha256").hexdigest()
    stored_hash = session.enrollment_key_hash
    saved = session.registration_state == "saved"
    if (stored_hash or saved) and (
        session.enrollment_platform != body["platform"]
        or not constant_time_compare(stored_hash, key_hash)
    ):
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    if saved:
        return {
            "connection_uuid": session.connection_uuid,
            "registration_state": "saved",
        }
    key = plan["target"]["deployment_key"]
    uuid = connection_uuid(session.space.uuid, key)
    payload = {
        "space_uuid": session.space.uuid,
        "name": f"OpenShell ({key})",
        "provider": "openshell",
        "endpoint": plan["target"]["endpoint"],
        "api_key": api_key,
        "image": plan["catalog"]["images"][body["platform"]]["worker"],
        "cpu": 1,
        "memory": "1Gi",
        "timeout_seconds": 30,
        "creator": session.creator.uuid,
        "modifier": session.creator.uuid,
    }
    result = enrollment_remote(
        "PUT",
        f"/api/v1/sandbox-enrollment/{uuid}",
        body=payload,
        base_url=plan["target"]["diagnosis_url"],
    )
    if (
        result.get("space_uuid") != session.space.uuid
        or result.get("uuid") != uuid
        or result.get("status") != "ready"
        or result.get("api_key_configured") is not True
    ):
        raise InstallationError("SANDBOX_ENROLLMENT_NOT_READY", 409)
    session.registration_state = "saved"
    session.connection_uuid = uuid
    session.enrollment_key_hash = key_hash
    session.enrollment_platform = body["platform"]
    session.save(
        update_fields=[
            "registration_state",
            "connection_uuid",
            "enrollment_key_hash",
            "enrollment_platform",
            "gmt_modified",
        ]
    )
    return {"connection_uuid": uuid, "registration_state": "saved"}


@transaction.atomic
def enable(*, session_id, actor, body):
    if body != {}:
        raise InstallationError()
    session = (
        SandboxInstallationSession.objects.select_for_update()
        .select_related("space")
        .get(pk=session_id)
    )
    require_space_permission(
        user=actor,
        space_id=session.space.uuid,
        permission=SANDBOX_CONNECTION_MANAGE,
        lock=True,
    )
    if (
        session.mode != "bootstrap"
        or session.revoked_at
        or session.registration_state != "saved"
        or not session.connection_uuid
    ):
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    plan = session.bootstrap_plan
    platform = session.enrollment_platform
    if platform not in plan["catalog"]["images"]:
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    key = plan["target"]["deployment_key"]
    expected = {
        "uuid": connection_uuid(session.space.uuid, key),
        "space_uuid": session.space.uuid,
        "name": f"OpenShell ({key})",
        "provider": "openshell",
        "endpoint": plan["target"]["endpoint"],
        "image": plan["catalog"]["images"][platform]["worker"],
        "cpu": 1,
        "memory": "1Gi",
        "timeout_seconds": 30,
    }
    current = enrollment_remote(
        "GET",
        f"/api/v1/sandbox-connection/{session.connection_uuid}",
        body={"space_uuid": session.space.uuid},
        base_url=plan["target"]["diagnosis_url"],
    )
    revision = current.get("config_revision")
    if (
        session.connection_uuid != expected["uuid"]
        or any(current.get(field) != value for field, value in expected.items())
        or current.get("api_key_configured") is not True
        or current.get("status") == "disabled"
        or type(revision) is not int
        or not 1 <= revision <= 9223372036854775807
    ):
        raise InstallationError("SANDBOX_REPORT_CONFLICT", 409)
    result = enrollment_remote(
        "POST",
        f"/api/v1/sandbox-enrollment/{session.connection_uuid}/enable",
        body={
            "space_uuid": session.space.uuid,
            "modifier": actor.uuid,
            "expected_config_revision": revision,
        },
        base_url=session.bootstrap_plan["target"]["diagnosis_url"],
    )
    if (
        result.get("space_uuid") != session.space.uuid
        or result.get("connection_uuid") != session.connection_uuid
        or type(result.get("require_sandbox")) is not bool
    ):
        raise InstallationError("SANDBOX_ENROLLMENT_NOT_READY", 409)
    return {
        "connection_uuid": session.connection_uuid,
        "binding": {
            "connection_uuid": session.connection_uuid,
            "require_sandbox": result["require_sandbox"],
        },
    }
