import logging
import re

import requests as http_requests
from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from apps.core.models import (
    ObservationEndpointConfig,
    Space,
    SpaceDatasourceBinding,
    SpaceObservationProject,
)
from apps.core.services.credential_provider import CredentialPair
from apps.core.services.credential_service import create_credential_records

logger = logging.getLogger(__name__)

PROVISION_FAILURE_MESSAGE = "Observation project provisioning failed"


class SpaceObservationProvisioningError(Exception):
    """Raised when upstream provisioning fails or local config is invalid."""


def _unwrap_provision_payload(data: dict) -> dict:
    if not isinstance(data, dict):
        raise SpaceObservationProvisioningError("Provisioning response is not a JSON object")
    current = data
    visited = set()
    while isinstance(current, dict):
        if "project" in current or "credentials" in current or "found" in current:
            return current
        payload = current.get("data")
        if not isinstance(payload, dict):
            return current
        marker = id(payload)
        if marker in visited:
            return current
        visited.add(marker)
        current = payload
    return data


def normalize_space_name(space_name: str) -> str:
    normalized = slugify(space_name or "")
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return (normalized or "space")[:64]


def build_project_name(space: Space) -> str:
    return f"aivision-{space.id}-{normalize_space_name(space.name)}"


def build_idempotency_key(space: Space, endpoint: ObservationEndpointConfig) -> str:
    return f"space-{space.id}-endpoint-{endpoint.id}"


def get_default_observation_endpoint() -> ObservationEndpointConfig:
    endpoint = ObservationEndpointConfig.objects.filter(
        is_default=1,
        status=1,
        gmt_deleted__isnull=True,
    ).first()
    if not endpoint:
        raise SpaceObservationProvisioningError(
            "未找到默认 ObservationEndpointConfig，请先创建。"
        )
    return endpoint


def _get_internal_api_base_url(endpoint: ObservationEndpointConfig) -> str:
    config = endpoint.config or {}
    base_url = (config.get("internal_api_base_url") or endpoint.base_url or "").rstrip("/")
    if not base_url:
        raise SpaceObservationProvisioningError(
            f"Observation endpoint {endpoint.id} 未配置 internal_api_base_url/base_url"
        )
    return base_url


def _get_organization_id(endpoint: ObservationEndpointConfig) -> str:
    organization_id = (endpoint.config or {}).get("organization_id")
    if not organization_id:
        raise SpaceObservationProvisioningError(
            f"Observation endpoint {endpoint.id} 未配置 organization_id"
        )
    return str(organization_id)


def _get_internal_auth_value() -> str:
    auth_value = getattr(settings, "LANGFUSE_INTERNAL_AUTH_VALUE", "")
    if not auth_value:
        raise SpaceObservationProvisioningError(
            "LANGFUSE_INTERNAL_AUTH_VALUE 未配置"
        )
    return auth_value


def _build_request_headers() -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-AIVISION-AUTH": _get_internal_auth_value(),
    }
    debug_cookie = getattr(settings, "LANGFUSE_DEBUG_COOKIE", "")
    if debug_cookie:
        headers["Cookie"] = debug_cookie
    return headers


def _build_provision_payload(
    space: Space,
    endpoint: ObservationEndpointConfig,
) -> tuple[dict, str]:
    idempotency_key = build_idempotency_key(space, endpoint)
    payload = {
        "spaceId": str(space.id),
        "spaceName": space.name,
        "organizationId": _get_organization_id(endpoint),
        "projectName": build_project_name(space),
        "idempotencyKey": idempotency_key,
        "metadata": {
            "source": "aivision",
            "spaceId": str(space.id),
            "spaceName": space.name,
        },
    }
    return payload, idempotency_key


def _parse_provision_response(data: dict) -> tuple[dict, CredentialPair, CredentialPair]:
    data = _unwrap_provision_payload(data)
    project = data.get("project") or {}
    credentials = data.get("credentials") or {}
    read = credentials.get("read") or {}
    write = credentials.get("write") or {}

    project_id = project.get("id")
    project_name = project.get("name")
    if not project_id or not project_name:
        logger.error("Provisioning response payload missing project info: %s", str(data)[:2000])
        raise SpaceObservationProvisioningError("Provisioning response missing project info")
    if not read.get("publicKey") or not read.get("secretKey"):
        logger.error("Provisioning response payload missing READ credential: %s", str(data)[:2000])
        raise SpaceObservationProvisioningError("Provisioning response missing READ credential")
    if not write.get("publicKey") or not write.get("secretKey"):
        logger.error("Provisioning response payload missing WRITE credential: %s", str(data)[:2000])
        raise SpaceObservationProvisioningError("Provisioning response missing WRITE credential")

    project_info = {
        "upstream_project_id": project_id,
        "upstream_project_name": project_name,
        "upstream_project_slug": project.get("slug", "") or project_name,
    }
    read_pair = CredentialPair(
        public_key=read["publicKey"],
        secret_key=read["secretKey"],
    )
    write_pair = CredentialPair(
        public_key=write["publicKey"],
        secret_key=write["secretKey"],
    )
    return project_info, read_pair, write_pair


def _request_provision_result(
    endpoint: ObservationEndpointConfig,
    idempotency_key: str,
) -> dict:
    base_url = _get_internal_api_base_url(endpoint)
    resp = http_requests.get(
        f"{base_url}/api/internal/aivision/spaces/provision",
        params={"idempotencyKey": idempotency_key},
        headers=_build_request_headers(),
        timeout=getattr(settings, "LANGFUSE_INTERNAL_API_TIMEOUT", 30),
    )
    resp.raise_for_status()
    data = _unwrap_provision_payload(resp.json())
    if not data.get("found"):
        raise SpaceObservationProvisioningError(
            f"Provisioning timeout and idempotencyKey not found: {idempotency_key}"
        )
    return data


def _call_provision_api(
    endpoint: ObservationEndpointConfig,
    payload: dict,
    idempotency_key: str,
) -> dict:
    base_url = _get_internal_api_base_url(endpoint)
    try:
        resp = http_requests.post(
            f"{base_url}/api/internal/aivision/spaces/provision",
            json=payload,
            headers=_build_request_headers(),
            timeout=getattr(settings, "LANGFUSE_INTERNAL_API_TIMEOUT", 30),
        )
        resp.raise_for_status()
        return _unwrap_provision_payload(resp.json())
    except http_requests.Timeout:
        logger.warning(
            "Provision request timed out, querying by idempotency key: %s",
            idempotency_key,
        )
        return _request_provision_result(endpoint, idempotency_key)
    except http_requests.RequestException as exc:
        response_text = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text[:1000]
            except Exception:
                response_text = ""
        raise SpaceObservationProvisioningError(
            f"Provisioning request failed: {exc}{f' | response={response_text}' if response_text else ''}"
        ) from exc


def _get_or_create_project(
    space: Space,
    endpoint: ObservationEndpointConfig,
) -> SpaceObservationProject:
    project = SpaceObservationProject.objects.filter(
        space=space,
        gmt_deleted__isnull=True,
    ).select_related("endpoint_config").first()
    if project:
        if project.endpoint_config_id != endpoint.id:
            raise SpaceObservationProvisioningError(
                "空间已绑定其他 ObservationEndpointConfig，当前不支持自动迁移。"
            )
        return project
    return SpaceObservationProject.objects.create(
        space=space,
        endpoint_config=endpoint,
        tenant_name=endpoint.tenant_name,
        status=1,
        provision_status=0,
    )


def _ensure_default_trace_binding(
    project: SpaceObservationProject,
    *,
    source_system: str = "langfuse",
) -> None:
    with transaction.atomic():
        binding, created = (
            SpaceDatasourceBinding.objects.select_for_update().get_or_create(
                space_observation_project=project,
                datasource_type="traces",
                source_system=source_system,
                defaults={
                    "enabled": 1,
                    "is_default": 1,
                    "config": {},
                },
            )
        )
        if created:
            SpaceDatasourceBinding.objects.filter(
                space_observation_project=project,
                datasource_type="traces",
            ).exclude(pk=binding.pk).update(is_default=0)
            return

        updated_fields: list[str] = []
        if binding.enabled != 1:
            binding.enabled = 1
            updated_fields.append("enabled")
        if binding.is_default != 1:
            binding.is_default = 1
            updated_fields.append("is_default")
        if binding.gmt_deleted is not None:
            binding.gmt_deleted = None
            updated_fields.append("gmt_deleted")
        if updated_fields:
            updated_fields.append("gmt_modified")
            binding.save(update_fields=updated_fields)
        SpaceDatasourceBinding.objects.filter(
            space_observation_project=project,
            datasource_type="traces",
        ).exclude(pk=binding.pk).update(is_default=0)


def provision_space_observation_project(
    space: Space,
    endpoint: ObservationEndpointConfig | None = None,
) -> SpaceObservationProject | None:
    if space.type != 2:
        return None

    endpoint = endpoint or get_default_observation_endpoint()
    project = _get_or_create_project(space, endpoint)
    if project.provision_status == 1:
        return project

    payload, idempotency_key = _build_provision_payload(space, endpoint)

    try:
        result = _call_provision_api(endpoint, payload, idempotency_key)
        project_info, read_pair, write_pair = _parse_provision_response(result)

        with transaction.atomic():
            project.endpoint_config = endpoint
            project.tenant_name = endpoint.tenant_name
            project.upstream_project_id = project_info["upstream_project_id"]
            project.upstream_project_name = project_info["upstream_project_name"]
            project.upstream_project_slug = project_info["upstream_project_slug"]
            project.provision_status = 1
            project.last_error = None
            project.status = 1
            project.save(
                update_fields=[
                    "endpoint_config",
                    "tenant_name",
                    "upstream_project_id",
                    "upstream_project_name",
                    "upstream_project_slug",
                    "provision_status",
                    "last_error",
                    "status",
                    "gmt_modified",
                ]
            )
            create_credential_records(project, read_pair, write_pair)
            _ensure_default_trace_binding(project, source_system="langfuse")

        return project
    except Exception as exc:
        project.provision_status = 2
        project.last_error = PROVISION_FAILURE_MESSAGE
        project.save(update_fields=["provision_status", "last_error", "gmt_modified"])
        logger.error(
            "Failed to provision observation project for space=%s endpoint=%s (%s)",
            space.id,
            endpoint.id,
            type(exc).__name__,
        )
        raise
