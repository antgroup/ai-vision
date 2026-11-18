"""Bounded authenticated plugin HTTP v1 transport; deliberately no provider SDK."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from agentlens.config import sandbox_input_limit
from agentlens.domain.sandbox import (
    LEGACY_INPUT_BYTES,
    MAX_INPUT_BYTES,
    SandboxConnection,
    SandboxError,
)

MAX_REQUEST_BYTES = MAX_INPUT_BYTES
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CREATE_TIMEOUT_SECONDS = 120
CLEANUP_TIMEOUT_SECONDS = 45
EXECUTION_CLEANUP_GRACE_SECONDS = 55
LEASE_PHASE_GRACE_SECONDS = (
    CREATE_TIMEOUT_SECONDS + CLEANUP_TIMEOUT_SECONDS + EXECUTION_CLEANUP_GRACE_SECONDS + 30
)


class SandboxCreateNotSent(SandboxError):
    """Connection establishment failed before the create request could be sent."""

    def __init__(self) -> None:
        super().__init__("sandbox_transport_error")


def _encode_date(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("Unsupported sandbox input value")


def encode_request(payload: dict, *, max_bytes: int | None = None) -> bytes:
    limit = sandbox_input_limit() if max_bytes is None else max_bytes
    if type(limit) is not int or not 1024 <= limit <= MAX_REQUEST_BYTES:
        raise SandboxError("sandbox_configuration_invalid")
    try:
        raw = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, default=_encode_date
        ).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise SandboxError("sandbox_configuration_invalid") from error
    if len(raw) > limit:
        raise SandboxError(
            "sandbox_input_limit",
            details={
                "stage": "request",
                "actual_bytes": len(raw),
                "allowed_bytes": limit,
            },
        )
    return raw


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    parts = urlsplit(endpoint)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise SandboxError("sandbox_configuration_invalid")
    try:
        _ = parts.port
    except ValueError as error:
        raise SandboxError("sandbox_configuration_invalid") from error
    return endpoint


def effective_input_limit(health: dict, image: str, client_limit: int) -> int:
    """Missing image-specific reader capability means the legacy bound, never infinity."""
    plugin_limit = health.get("max_input_bytes", LEGACY_INPUT_BYTES)
    entries = health.get("input_limits", [])
    if (
        type(plugin_limit) is not int
        or not 1024 <= plugin_limit <= MAX_INPUT_BYTES
        or not isinstance(entries, list)
        or len(entries) > 128
    ):
        raise SandboxError("sandbox_protocol_error")
    worker_limit = LEGACY_INPUT_BYTES
    for entry in entries:
        if not isinstance(entry, dict):
            raise SandboxError("sandbox_protocol_error")
        if entry.get("image") == image:
            value = entry.get("max_input_bytes")
            if type(value) is not int or not 1024 <= value <= MAX_INPUT_BYTES:
                raise SandboxError("sandbox_protocol_error")
            worker_limit = value
            break
    return min(client_limit, plugin_limit, worker_limit)


class SandboxClient:
    def __init__(self, connection: SandboxConnection, *, transport=None) -> None:
        self.endpoint = normalize_endpoint(connection.endpoint)
        token = connection.api_key
        if not token or len(token) > 8192 or any(not 33 <= ord(char) <= 126 for char in token):
            raise SandboxError("sandbox_configuration_invalid")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, payload=None, *, timeout: float = 10) -> dict:
        raw = encode_request(payload) if payload is not None else None
        deadline = time.monotonic() + timeout
        try:
            with self._client.stream(
                method,
                self.endpoint + path,
                content=raw,
                timeout=httpx.Timeout(timeout, connect=min(timeout, 5)),
            ) as response:
                if response.status_code in {401, 403}:
                    raise SandboxError("sandbox_authentication_failed")
                if response.status_code == 409:
                    raise SandboxError("sandbox_lease_conflict")
                if response.status_code == 404 and path == "/v1/executions/validate":
                    raise SandboxError("sandbox_plugin_upgrade_required")
                error_response = not 200 <= response.status_code < 300
                if error_response and response.status_code not in {413, 422}:
                    raise SandboxError("sandbox_transport_error")
                content = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise SandboxError("sandbox_transport_error")
                    if len(content) + len(chunk) > (4096 if error_response else MAX_RESPONSE_BYTES):
                        if error_response:
                            raise SandboxError("sandbox_transport_error")
                        raise SandboxError("sandbox_output_limit")
                    content.extend(chunk)
                if error_response:
                    try:
                        error_body = json.loads(content)
                    except (ValueError, UnicodeError, RecursionError):
                        error_body = None
                    if isinstance(error_body, dict):
                        if (
                            response.status_code == 413
                            and error_body.get("code") == "SANDBOX_INPUT_TOO_LARGE"
                        ):
                            raise SandboxError(
                                "sandbox_input_limit", details=error_body.get("details")
                            )
                        if (
                            response.status_code == 422
                            and error_body.get("code") == "SANDBOX_RUNTIME_UNAVAILABLE"
                        ):
                            raise SandboxError("sandbox_runtime_unavailable")
                        if (
                            response.status_code == 422
                            and error_body.get("code") == "SANDBOX_INPUT_INVALID"
                        ):
                            raise SandboxError("sandbox_configuration_invalid")
                    raise SandboxError("sandbox_transport_error")
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            if method == "POST" and path == "/v1/sandboxes":
                raise SandboxCreateNotSent() from error
            raise SandboxError("sandbox_transport_error") from error
        except httpx.HTTPError as error:
            raise SandboxError("sandbox_transport_error") from error
        try:
            parsed = json.loads(content)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise SandboxError("sandbox_protocol_error") from error
        if not isinstance(parsed, dict):
            raise SandboxError("sandbox_protocol_error")
        return parsed

    def health(self) -> dict:
        data = self._request("GET", "/v1/health")
        if (
            type(data.get("protocol_version")) is not int
            or data["protocol_version"] != 1
            or data.get("provider") != "openshell"
            or data.get("ready") is not True
            or not isinstance(data.get("provider_version"), str)
            or not isinstance(data.get("capabilities"), list)
            or "custom_rule" not in data["capabilities"]
        ):
            raise SandboxError("sandbox_unavailable")
        return data

    def create(self, payload: dict) -> dict:
        data = self._request("POST", "/v1/sandboxes", payload, timeout=CREATE_TIMEOUT_SECONDS)
        if (
            data.get("lease_id") != payload["lease_id"]
            or data.get("state") != "ready"
            or not isinstance(data.get("sandbox_id"), str)
            or not data["sandbox_id"]
            or data.get("cleanup_verified") is not False
        ):
            raise SandboxError("sandbox_protocol_error")
        return data

    def validate_execution(self, create: dict, execute: dict) -> dict:
        data = self._request(
            "POST", "/v1/executions/validate", {"create": create, "execute": execute}
        )
        if (
            data.get("valid") is not True
            or type(data.get("input_bytes")) is not int
            or type(data.get("max_input_bytes")) is not int
            or not 0 < data["input_bytes"] <= data["max_input_bytes"] <= MAX_REQUEST_BYTES
            or data.get("transport") not in {"unary", "streaming"}
        ):
            raise SandboxError("sandbox_protocol_error")
        return data

    def execute(self, lease_id: str, payload: dict) -> dict:
        return self._request(
            "POST",
            f"/v1/sandboxes/{lease_id}/execute",
            payload,
            timeout=payload["timeout_seconds"] + EXECUTION_CLEANUP_GRACE_SECONDS,
        )

    def delete(self, lease_id: str) -> bool:
        data = self._request("DELETE", f"/v1/sandboxes/{lease_id}", timeout=CLEANUP_TIMEOUT_SECONDS)
        return (
            data.get("lease_id") == lease_id
            and data.get("state") == "closed"
            and data.get("cleanup_verified") is True
        )
