"""Space-authorized, credential-redacting proxy for optional sandbox resources."""

import re

import requests
from rest_framework.views import APIView

from ..permissions.constants import SANDBOX_CONNECTION_MANAGE, SANDBOX_CONNECTION_READ
from .agentlens_proxy import (
    _apply_create_audit_fields,
    _apply_update_audit_fields,
    _audit_log,
    _check_space,
    _lens_headers,
    _lens_url,
    _scope_error,
)
from .common import error_response, success_response

CONNECTION_FIELDS = frozenset(
    {
        "uuid",
        "space_uuid",
        "name",
        "provider",
        "endpoint",
        "api_key_configured",
        "image",
        "cpu",
        "memory",
        "timeout_seconds",
        "config_revision",
        "status",
        "gmt_create",
        "gmt_modified",
    }
)
INPUT_FIELDS = frozenset(
    {
        "name",
        "provider",
        "endpoint",
        "api_key",
        "image",
        "cpu",
        "memory",
        "timeout_seconds",
        "status",
    }
)
TEST_ERRORS = frozenset(
    {
        "authentication_failed",
        "permission_denied",
        "timeout",
        "connection_failed",
        "invalid_request",
        "configuration_changed",
        "unavailable",
        "protocol_mismatch",
        "provider_not_ready",
        "invalid_response",
        "unsupported_provider",
        "disabled",
        "sandbox_configuration_invalid",
        "sandbox_authentication_failed",
        "sandbox_protocol_error",
        "sandbox_unavailable",
        "sandbox_output_limit",
        "sandbox_transport_error",
        "sandbox_lease_conflict",
    }
)
ERROR_CODES = frozenset(
    {
        "validation",
        "not_found",
        "conflict",
        "forbidden",
        "external_unavailable",
        "sandbox_unavailable",
        "sandbox_required",
        "sandbox_connection_in_use",
    }
)


def _failure(status=502, code="external_unavailable"):
    # No provider body, exception, URL or credential may cross this boundary.
    return error_response(
        "Sandbox request failed",
        {404: 4040, 409: 4090, 422: 4220}.get(status, 5001),
        status=status,
        data={
            "agentlens_code": (
                code
                if isinstance(code, str) and code in ERROR_CODES
                else "external_unavailable"
            )
        },
    )


def _remote(method, path, *, body=None, space_id=None):
    try:
        response = getattr(requests, method)(
            _lens_url(path),
            headers=_lens_headers(),
            **(
                {"params": {"space_uuid": space_id}}
                if method in {"get", "delete"}
                else {"json": body}
            ),
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException:
        return None, _failure()
    if response.status_code == 204:
        return None, None
    try:
        data = response.json()
    except ValueError:
        return None, _failure()
    if not response.ok:
        status = (
            response.status_code if response.status_code in {404, 409, 422} else 502
        )
        return None, _failure(
            status, data.get("code") if isinstance(data, dict) else None
        )
    return data, None


def _connection(data, space_id):
    if not isinstance(data, dict) or str(data.get("space_uuid", "")) != str(space_id):
        return None, _scope_error()
    clean = {key: value for key, value in data.items() if key in CONNECTION_FIELDS}
    if "test_error" in data:
        reason = data["test_error"]
        clean["test_error"] = (
            reason
            if isinstance(reason, str) and reason in TEST_ERRORS
            else (None if reason is None else "unavailable")
        )
    return clean, None


def _owned(connection_uuid, space_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", connection_uuid):
        return None, _failure(422, "validation")
    data, error = _remote(
        "get", f"/api/v1/sandbox-connection/{connection_uuid}", space_id=space_id
    )
    if error is not None:
        return None, error
    return _connection(data, space_id)


def _input(request, space_id, *, create=False):
    if not isinstance(request.data, dict):
        return None
    body = {key: value for key, value in request.data.items() if key in INPUT_FIELDS}
    if not create and "expected_config_revision" in request.data:
        body["expected_config_revision"] = request.data["expected_config_revision"]
    body["space_uuid"] = space_id
    return (_apply_create_audit_fields if create else _apply_update_audit_fields)(
        body, request
    )


class SandboxProvidersView(APIView):
    def get(self, request, space_id):
        _check_space(request, space_id, SANDBOX_CONNECTION_READ)
        data, error = _remote("get", "/api/v1/sandbox-providers", space_id=space_id)
        if error is not None:
            return error
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return _failure()
        fields = {"id", "name", "protocol_version", "capabilities"}
        items = [
            {key: value for key, value in item.items() if key in fields}
            for item in data["items"]
            if isinstance(item, dict) and item.get("id") == "openshell"
        ]
        return success_response({"items": items, "total": len(items)})


class SandboxConnectionListView(APIView):
    def get(self, request, space_id):
        _check_space(request, space_id, SANDBOX_CONNECTION_READ)
        data, error = _remote("get", "/api/v1/sandbox-connections", space_id=space_id)
        if error is not None:
            return error
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return _failure()
        items = []
        for item in data["items"]:
            clean, error = _connection(item, space_id)
            if error is not None:
                return error
            items.append(clean)
        return success_response({"items": items, "total": len(items)})

    def post(self, request, space_id):
        _check_space(request, space_id, SANDBOX_CONNECTION_MANAGE)
        body = _input(request, space_id, create=True)
        if body is None:
            return _failure(422, "validation")
        data, error = _remote("post", "/api/v1/sandbox-connection", body=body)
        if error is not None:
            return error
        clean, error = _connection(data, space_id)
        _audit_log(request, space_id=space_id, action="sandbox_connection.create")
        return error if error is not None else success_response(clean)


class SandboxConnectionDetailView(APIView):
    def get(self, request, space_id, connection_uuid):
        _check_space(request, space_id, SANDBOX_CONNECTION_READ)
        clean, error = _owned(connection_uuid, space_id)
        return error if error is not None else success_response(clean)

    def patch(self, request, space_id, connection_uuid):
        _check_space(request, space_id, SANDBOX_CONNECTION_MANAGE)
        _, error = _owned(connection_uuid, space_id)
        if error is not None:
            return error
        body = _input(request, space_id)
        if body is None:
            return _failure(422, "validation")
        data, error = _remote(
            "patch", f"/api/v1/sandbox-connection/{connection_uuid}", body=body
        )
        if error is not None:
            return error
        clean, error = _connection(data, space_id)
        _audit_log(
            request,
            space_id=space_id,
            action="sandbox_connection.update",
            target=connection_uuid,
        )
        return error if error is not None else success_response(clean)

    def delete(self, request, space_id, connection_uuid):
        _check_space(request, space_id, SANDBOX_CONNECTION_MANAGE)
        _, error = _owned(connection_uuid, space_id)
        if error is not None:
            return error
        _, error = _remote(
            "delete", f"/api/v1/sandbox-connection/{connection_uuid}", space_id=space_id
        )
        _audit_log(
            request,
            space_id=space_id,
            action="sandbox_connection.delete",
            target=connection_uuid,
        )
        return error if error is not None else success_response(None)


class SandboxConnectionRuntimeView(APIView):
    def get(self, request, space_id, connection_uuid):
        _check_space(request, space_id, SANDBOX_CONNECTION_READ)
        owned, error = _owned(connection_uuid, space_id)
        if error is not None:
            return error
        data, error = _remote(
            "get",
            f"/api/v1/sandbox-connection/{connection_uuid}/runtime",
            space_id=space_id,
        )
        if error is not None:
            return error
        if (
            not isinstance(data, dict)
            or data.get("connection_uuid") != connection_uuid
            or data.get("config_revision") != owned.get("config_revision")
            or data.get("image") != owned.get("image")
        ):
            return _scope_error()
        fields = {
            "connection_uuid",
            "config_revision",
            "image",
            "ready",
            "capabilities",
            "runtime_manifests",
            "effective_timeout_seconds",
        }
        return success_response(
            {key: value for key, value in data.items() if key in fields}
        )


class SandboxConnectionTestView(APIView):
    def post(self, request, space_id, connection_uuid):
        _check_space(request, space_id, SANDBOX_CONNECTION_MANAGE)
        _, error = _owned(connection_uuid, space_id)
        if error is not None:
            return error
        data, error = _remote(
            "post",
            f"/api/v1/sandbox-connection/{connection_uuid}/test",
            body={"space_uuid": space_id},
        )
        if error is not None:
            return error
        clean, error = _connection(data, space_id)
        _audit_log(
            request,
            space_id=space_id,
            action="sandbox_connection.test",
            target=connection_uuid,
        )
        return error if error is not None else success_response(clean)


class SandboxBindingView(APIView):
    def _response(self, data, space_id):
        if not isinstance(data, dict) or str(data.get("space_uuid", "")) != str(
            space_id
        ):
            return _scope_error()
        return success_response(
            {
                key: data.get(key)
                for key in ("space_uuid", "connection_uuid", "require_sandbox")
            }
        )

    def get(self, request, space_id):
        _check_space(request, space_id, SANDBOX_CONNECTION_READ)
        data, error = _remote("get", "/api/v1/sandbox-binding", space_id=space_id)
        return error if error is not None else self._response(data, space_id)

    def patch(self, request, space_id):
        _check_space(request, space_id, SANDBOX_CONNECTION_MANAGE)
        if not isinstance(request.data, dict):
            return _failure(422, "validation")
        body = {
            key: value
            for key, value in request.data.items()
            if key in {"connection_uuid", "require_sandbox"}
        }
        connection_uuid = body.get("connection_uuid")
        if connection_uuid is not None:
            if not isinstance(connection_uuid, str):
                return _failure(422, "validation")
            _, error = _owned(connection_uuid, space_id)
            if error is not None:
                return error
        body["space_uuid"] = space_id
        _apply_update_audit_fields(body, request)
        data, error = _remote("patch", "/api/v1/sandbox-binding", body=body)
        _audit_log(request, space_id=space_id, action="sandbox_connection.bind")
        return error if error is not None else self._response(data, space_id)
