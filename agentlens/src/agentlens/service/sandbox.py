"""Resolve optional routing by identity; health never chooses a local fallback."""

from typing import Any

from agentlens.domain.sandbox import PROFILE_FIELDS, SandboxConnection, SandboxError, SandboxStatus
from agentlens.infra.ob.tables.sandbox_table import SandboxTable


def resolve_sandbox_profile(
    table: SandboxTable, space_uuid: str, sandbox: dict | None = None, *, for_update: bool = False
) -> dict[str, Any]:
    if sandbox is not None and not isinstance(sandbox, dict):
        raise SandboxError("sandbox_configuration_invalid")
    options = sandbox or {}
    if set(options) - {"mode", "connection_uuid"}:
        raise SandboxError("sandbox_configuration_invalid")
    mode = options.get("mode", "inherit")
    connection_uuid = options.get("connection_uuid")
    if (
        not isinstance(mode, str)
        or mode not in {"inherit", "required", "local"}
        or (
            connection_uuid is not None
            and (not isinstance(connection_uuid, str) or not connection_uuid)
        )
    ):
        raise SandboxError("sandbox_configuration_invalid")
    binding = table.get_binding(space_uuid, for_update=for_update)
    if mode == "local":
        if connection_uuid is not None:
            raise SandboxError("sandbox_configuration_invalid")
        if binding.require_sandbox:
            raise SandboxError("sandbox_required")
        return {"mode": "local"}
    selected = connection_uuid or binding.connection_uuid
    if not selected:
        if mode == "required" or binding.require_sandbox:
            raise SandboxError("sandbox_required")
        return {"mode": "local"}
    connection = table.get_in_space(selected, space_uuid, for_update=for_update)
    if connection is None:
        raise SandboxError("sandbox_connection_missing")
    # A selected unavailable/disabled connection remains selected. Runtime fails closed.
    return connection.frozen_profile()


def resolve_sandbox_credentials(table: SandboxTable, profile: dict) -> SandboxConnection:
    if not isinstance(profile, dict) or profile.get("mode") != "sandbox":
        raise SandboxError("sandbox_configuration_invalid")
    if profile.get("protocol_version") != 1:
        raise SandboxError("sandbox_configuration_invalid")
    uuid, space_uuid = profile.get("connection_uuid"), profile.get("space_uuid")
    if not isinstance(uuid, str) or not isinstance(space_uuid, str):
        raise SandboxError("sandbox_configuration_invalid")
    connection = table.get_in_space(uuid, space_uuid)
    if connection is None:
        raise SandboxError("sandbox_connection_missing")
    if any(profile.get(key) != getattr(connection, key) for key in PROFILE_FIELDS):
        raise SandboxError("sandbox_identity_changed")
    if connection.status != SandboxStatus.READY or not connection.api_key:
        raise SandboxError("sandbox_unavailable")
    return connection
