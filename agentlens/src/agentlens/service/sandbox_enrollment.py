"""Idempotent deployment-host enrollment and explicitly verified Space defaults."""

import re
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from agentlens.domain.errors import Conflict, NotFound, Validation
from agentlens.domain.sandbox import PROFILE_FIELDS, SandboxConnection, SandboxError, SandboxStatus
from agentlens.infra.ob.tables.sandbox_table import SandboxTable
from agentlens.infra.sandbox import SandboxClient


def _validate_identity(uuid: str, fields: dict) -> None:
    name = fields.get("name", "")
    match = re.fullmatch(r"OpenShell \(([a-f0-9]{16})\)", name)
    if match is None:
        raise Validation("Sandbox enrollment requires a fixed deployment profile.")
    key = match[1]
    expected = uuid5(NAMESPACE_URL, f"aivision:sandbox:{fields['space_uuid']}:{key}").hex
    if (
        uuid != expected
        or fields.get("provider") != "openshell"
        or fields.get("endpoint") != f"http://aiv-openshell-{key}:8091"
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64}", fields.get("image", "")
        )
        or fields.get("cpu") != 1
        or fields.get("memory") != "1Gi"
        or fields.get("timeout_seconds") != 30
    ):
        raise Validation("Sandbox enrollment requires a fixed deployment profile.")


def _probe(
    table: SandboxTable,
    uuid: str,
    space_uuid: str,
    *,
    expected: SandboxConnection | None = None,
    expected_revision: int | None = None,
) -> SandboxConnection:
    connection = table.probe_snapshot(uuid, space_uuid)
    if connection is None:
        raise NotFound("Sandbox connection not found in this Space.")
    if connection.status == SandboxStatus.DISABLED:
        raise Conflict("Sandbox enrollment is disabled.")
    _validate_identity(uuid, connection.__dict__)
    if (
        expected is not None
        and any(
            getattr(connection, field) != getattr(expected, field)
            for field in (*PROFILE_FIELDS, "name", "api_key")
        )
    ) or (expected_revision is not None and connection.config_revision != expected_revision):
        raise Conflict("Sandbox connection changed before verification.")
    client = SandboxClient(connection)
    try:
        client.health()
    except SandboxError:
        table.save_test_status(connection, SandboxStatus.UNAVAILABLE)
        raise
    finally:
        client.close()
    return connection


def enroll(table: SandboxTable, uuid: str, fields: dict) -> SandboxConnection:
    _validate_identity(uuid, fields)
    now = datetime.now()
    connection = SandboxConnection(
        uuid=uuid,
        **fields,
        config_revision=1,
        status=SandboxStatus.UNTESTED,
        gmt_create=now,
        gmt_modified=now,
    )
    pinned = table.put_enrollment(connection)
    verified = _probe(table, uuid, fields["space_uuid"], expected=pinned)
    if not table.save_test_status(verified, SandboxStatus.READY):
        raise Conflict("Sandbox connection changed during verification.")
    current = table.get_in_space(uuid, fields["space_uuid"])
    if current is None:
        raise NotFound("Sandbox connection not found in this Space.")
    return current


def enable(
    table: SandboxTable,
    uuid: str,
    space_uuid: str,
    modifier: str | None,
    expected_revision: int | None = None,
):
    verified = _probe(table, uuid, space_uuid, expected_revision=expected_revision)
    _validate_identity(uuid, verified.__dict__)
    return table.enable_verified_enrollment(verified, modifier)
