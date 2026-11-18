"""Persistence primitives for sandbox resources; no provider I/O."""

from __future__ import annotations

from datetime import datetime, timedelta
from secrets import compare_digest
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentlens.domain.errors import Conflict, NotFound
from agentlens.domain.sandbox import (
    PROFILE_FIELDS,
    SandboxBinding,
    SandboxConnection,
    SandboxError,
    SandboxStatus,
)
from agentlens.infra.ob.models import (
    DiagnoserModel,
    SandboxBindingModel,
    SandboxConnectionModel,
    SandboxLeaseModel,
    TaskModel,
)
from agentlens.infra.sandbox import LEASE_PHASE_GRACE_SECONDS


def _connection(model: SandboxConnectionModel) -> SandboxConnection:
    keys = ("uuid", "name", "api_key", "gmt_create", "gmt_modified", "creator", "modifier")
    return SandboxConnection(
        **{key: getattr(model, key) for key in (*PROFILE_FIELDS, *keys)},
        status=SandboxStatus(model.status),
    )


class SandboxTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_in_space(
        self, uuid: str, space_uuid: str, *, for_update: bool = False
    ) -> SandboxConnection | None:
        query = select(SandboxConnectionModel).where(
            SandboxConnectionModel.uuid == uuid, SandboxConnectionModel.space_uuid == space_uuid
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        model = self.s.scalar(query)
        return _connection(model) if model else None

    def list(self, space_uuid: str) -> list[SandboxConnection]:
        return [
            _connection(row)
            for row in self.s.scalars(
                select(SandboxConnectionModel)
                .where(SandboxConnectionModel.space_uuid == space_uuid)
                .order_by(
                    SandboxConnectionModel.gmt_create.desc(), SandboxConnectionModel.id.desc()
                )
            )
        ]

    def insert(self, connection: SandboxConnection) -> None:
        fields = {key: getattr(connection, key) for key in connection.__dataclass_fields__}
        fields["status"] = connection.status.value
        self.s.add(SandboxConnectionModel(**fields))
        self.s.commit()

    def put_enrollment(self, connection: SandboxConnection) -> SandboxConnection:
        """Insert one deterministic identity or preserve an identical enrollment."""
        query = select(SandboxConnectionModel).where(SandboxConnectionModel.uuid == connection.uuid)
        model = self.s.scalar(query.with_for_update().execution_options(populate_existing=True))
        if model is None:
            try:
                self.insert(connection)
                return connection
            except IntegrityError:
                self.s.rollback()
                model = self.s.scalar(
                    query.with_for_update().execution_options(populate_existing=True)
                )
                if model is None:
                    raise Conflict(
                        "Sandbox enrollment conflicts with an existing resource."
                    ) from None
        current = _connection(model)
        fields = (*PROFILE_FIELDS, "name")
        if (
            any(
                getattr(current, field) != getattr(connection, field)
                for field in fields
                if field != "config_revision"
            )
            or not compare_digest(current.api_key.encode(), connection.api_key.encode())
            or current.status == SandboxStatus.DISABLED
        ):
            self.s.rollback()
            raise Conflict("Sandbox enrollment no longer matches the fixed profile.")
        self.s.rollback()
        return current

    def enable_verified_enrollment(
        self, snapshot: SandboxConnection, modifier: str | None
    ) -> SandboxBinding:
        """Commit readiness and default binding together after an unlocked probe."""
        binding = self.get_binding(snapshot.space_uuid, for_update=True)
        current = self.get_in_space(snapshot.uuid, snapshot.space_uuid, for_update=True)
        if current is None:
            self.s.rollback()
            raise NotFound("Sandbox connection not found in this Space.")
        if (
            current.status == SandboxStatus.DISABLED
            or any(
                getattr(current, field) != getattr(snapshot, field)
                for field in (*PROFILE_FIELDS, "name")
            )
            or not compare_digest(current.api_key.encode(), snapshot.api_key.encode())
        ):
            self.s.rollback()
            raise Conflict("Sandbox connection changed during verification.")
        now = datetime.now()
        self.s.execute(
            update(SandboxConnectionModel)
            .where(
                SandboxConnectionModel.uuid == snapshot.uuid,
                SandboxConnectionModel.space_uuid == snapshot.space_uuid,
            )
            .values(status=SandboxStatus.READY.value, gmt_modified=now)
        )
        self.s.execute(
            update(SandboxBindingModel)
            .where(SandboxBindingModel.space_uuid == snapshot.space_uuid)
            .values(connection_uuid=snapshot.uuid, modifier=modifier, gmt_modified=now)
        )
        self.s.commit()
        return SandboxBinding(snapshot.space_uuid, snapshot.uuid, binding.require_sandbox, modifier)

    def update(self, uuid: str, space_uuid: str, fields: dict[str, Any]) -> None:
        current = self.get_in_space(uuid, space_uuid, for_update=True)
        if current is None:
            raise NotFound("Sandbox connection not found in this Space.")
        fields = dict(fields)
        expected_revision = fields.pop("expected_config_revision", None)
        if expected_revision is not None and expected_revision != current.config_revision:
            raise Conflict("Sandbox connection configuration revision changed; refresh and retry.")
        # A token for one endpoint must never be implicitly sent to another destination.
        if fields.get("endpoint", current.endpoint) != current.endpoint and not fields.get(
            "api_key"
        ):
            raise SandboxError("sandbox_configuration_invalid")
        changed_profile = any(
            key in fields and fields[key] != getattr(current, key)
            for key in PROFILE_FIELDS
            if key not in {"space_uuid", "config_revision"}
        )
        if (
            changed_profile
            and self.s.scalar(
                select(SandboxLeaseModel.id)
                .where(
                    SandboxLeaseModel.connection_uuid == uuid,
                    SandboxLeaseModel.cleanup_verified == 0,
                )
                .limit(1)
            )
            is not None
        ):
            raise Conflict("Verify cleanup of active Sandbox leases before changing this profile.")
        if changed_profile:
            fields["config_revision"] = current.config_revision + 1
        if changed_profile:
            fields["status"] = (
                SandboxStatus.DISABLED.value
                if fields.get("status", current.status) == SandboxStatus.DISABLED
                else SandboxStatus.UNTESTED.value
            )
        fields["gmt_modified"] = datetime.now()
        self.s.execute(
            update(SandboxConnectionModel)
            .where(
                SandboxConnectionModel.uuid == uuid, SandboxConnectionModel.space_uuid == space_uuid
            )
            .values(**fields)
        )
        self.s.commit()

    def probe_snapshot(self, uuid: str, space_uuid: str) -> SandboxConnection | None:
        snapshot = self.get_in_space(uuid, space_uuid)
        self.s.rollback()
        return snapshot

    def save_test_status(self, snapshot: SandboxConnection, status: SandboxStatus) -> bool:
        result = self.s.execute(
            update(SandboxConnectionModel)
            .where(
                SandboxConnectionModel.uuid == snapshot.uuid,
                SandboxConnectionModel.space_uuid == snapshot.space_uuid,
                SandboxConnectionModel.config_revision == snapshot.config_revision,
                SandboxConnectionModel.api_key == snapshot.api_key,
                SandboxConnectionModel.status != SandboxStatus.DISABLED.value,
            )
            .values(status=status.value, gmt_modified=datetime.now())
        )
        self.s.commit()
        return result.rowcount == 1

    def get_binding(self, space_uuid: str, *, for_update: bool = False) -> SandboxBinding:
        if for_update:
            # Freeze must serialize on a real Space row, including first use.
            # A missing-row gap lock alone does not serialize two readers or
            # prevent different connection-lock orders across concurrent Jobs.
            values = {
                "space_uuid": space_uuid,
                "connection_uuid": None,
                "require_sandbox": 0,
                "gmt_create": datetime.now(),
                "gmt_modified": datetime.now(),
            }
            if self.s.get_bind().dialect.name == "sqlite":
                statement = sqlite_insert(SandboxBindingModel).values(**values)
                statement = statement.on_conflict_do_nothing(index_elements=["space_uuid"])
            else:
                statement = mysql_insert(SandboxBindingModel).values(**values)
                statement = statement.on_duplicate_key_update(space_uuid=space_uuid)
            self.s.execute(statement)
        query = select(SandboxBindingModel).where(SandboxBindingModel.space_uuid == space_uuid)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = self.s.scalar(query)
        if row is None:
            return SandboxBinding(space_uuid)
        return SandboxBinding(
            row.space_uuid, row.connection_uuid, bool(row.require_sandbox), row.modifier
        )

    def save_binding(self, binding: SandboxBinding) -> None:
        # Match Task-freeze lock order: binding, then selected connection.
        self.get_binding(binding.space_uuid, for_update=True)
        current = self.s.scalar(
            select(SandboxBindingModel)
            .where(SandboxBindingModel.space_uuid == binding.space_uuid)
            .with_for_update()
        )
        if binding.connection_uuid:
            if (
                self.get_in_space(binding.connection_uuid, binding.space_uuid, for_update=True)
                is None
            ):
                raise NotFound("Sandbox connection not found in this Space.")
        if current is None:
            current = SandboxBindingModel(space_uuid=binding.space_uuid)
            self.s.add(current)
        current.connection_uuid = binding.connection_uuid
        current.require_sandbox = int(binding.require_sandbox)
        current.modifier = binding.modifier
        current.gmt_modified = datetime.now()
        self.s.commit()

    def delete(self, uuid: str, space_uuid: str) -> None:
        if self.get_in_space(uuid, space_uuid, for_update=True) is None:
            raise NotFound("Sandbox connection not found in this Space.")
        bound = self.s.scalar(
            select(SandboxBindingModel.id).where(SandboxBindingModel.connection_uuid == uuid)
        )
        active_lease = self.s.scalar(
            select(SandboxLeaseModel.id)
            .where(
                SandboxLeaseModel.connection_uuid == uuid,
                SandboxLeaseModel.cleanup_verified == 0,
            )
            .limit(1)
        )
        referenced = any(
            isinstance(spec, dict)
            and isinstance(spec.get("sandbox"), dict)
            and spec["sandbox"].get("connection_uuid") == uuid
            for spec in self.s.scalars(
                select(DiagnoserModel.spec).where(DiagnoserModel.space_uuid == space_uuid)
            )
        )
        active_snapshot = any(
            _contains_connection(snapshot, uuid)
            for snapshot in self.s.scalars(
                select(TaskModel.execution_snapshot).where(
                    TaskModel.status.in_(("pending", "dispatched", "running"))
                )
            )
        )
        if bound is not None or active_lease is not None or referenced or active_snapshot:
            raise Conflict(
                "Sandbox connection is referenced by a binding, diagnoser or active execution."
            )
        self.s.execute(delete(SandboxConnectionModel).where(SandboxConnectionModel.uuid == uuid))
        self.s.commit()

    def reserve_lease(
        self,
        fields: dict[str, Any],
        *,
        profile: dict | None = None,
        worker_uuid: str | None = None,
        runtime_spec: dict | None = None,
    ) -> None:
        task = self.s.scalar(
            select(TaskModel)
            .where(TaskModel.uuid == fields["task_uuid"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            if not (
                fields["task_uuid"].startswith("trial-")
                and fields["attempt"] == 0
                and fields["diagnoser_uuid"] == "trial"
                and worker_uuid is None
            ):
                raise SandboxError("sandbox_attempt_rejected")
        else:
            snapshot = task.execution_snapshot
            if (
                task.status != "running"
                or task.attempt != fields["attempt"]
                or not worker_uuid
                or task.worker != worker_uuid
                or not isinstance(snapshot, dict)
                or not isinstance(snapshot.get("job"), dict)
                or snapshot["job"].get("space_uuid") != fields["space_uuid"]
            ):
                raise SandboxError("sandbox_attempt_rejected")
            definition = next(
                (
                    item
                    for item in snapshot.get("diagnosers", [])
                    if isinstance(item, dict) and item.get("uuid") == fields["diagnoser_uuid"]
                ),
                None,
            )
            if (
                not isinstance(definition, dict)
                or profile is None
                or definition.get("spec", {}).get("sandbox") != profile
                or {
                    key: definition.get("spec", {})[key]
                    for key in ("rule_runtime", "rule_runtime_manifest", "materials")
                    if key in definition.get("spec", {})
                }
                != (runtime_spec or {})
            ):
                raise SandboxError("sandbox_attempt_rejected")
        # Share the connection row lock with profile update/delete so cleanup's
        # credential destination stays stable until this lease is closed.
        connection = self.get_in_space(
            fields["connection_uuid"], fields["space_uuid"], for_update=True
        )
        if connection is None:
            raise SandboxError("sandbox_connection_missing")
        if profile is not None and any(
            profile.get(key) != getattr(connection, key) for key in PROFILE_FIELDS
        ):
            raise SandboxError("sandbox_identity_changed")
        if profile is not None and connection.status != SandboxStatus.READY:
            raise SandboxError("sandbox_unavailable")
        row = self.s.scalar(
            select(SandboxLeaseModel).where(SandboxLeaseModel.uuid == fields["uuid"])
        )
        if row is not None:
            raise SandboxError("sandbox_lease_conflict")
        self.s.add(
            SandboxLeaseModel(
                **fields,
                state="creating",
                cleanup_verified=0,
                expires_at=datetime.now()
                + timedelta(seconds=connection.timeout_seconds + LEASE_PHASE_GRACE_SECONDS),
            )
        )
        try:
            self.s.commit()
        except IntegrityError as error:
            self.s.rollback()
            raise SandboxError("sandbox_lease_conflict") from error

    def finish_lease(self, uuid: str, *, cleanup_verified: bool) -> None:
        self.s.execute(
            update(SandboxLeaseModel)
            .where(SandboxLeaseModel.uuid == uuid, SandboxLeaseModel.cleanup_verified == 0)
            .values(
                state="closed" if cleanup_verified else "cleanup_pending",
                cleanup_verified=int(cleanup_verified),
                gmt_modified=datetime.now(),
            )
        )
        self.s.commit()

    def pending_cleanup(self, batch_limit: int) -> list[tuple[str, SandboxConnection]]:
        rows = self.s.execute(
            select(SandboxLeaseModel.uuid, SandboxConnectionModel)
            .join(
                SandboxConnectionModel,
                SandboxConnectionModel.uuid == SandboxLeaseModel.connection_uuid,
            )
            .where(
                SandboxLeaseModel.cleanup_verified == 0,
                or_(
                    SandboxLeaseModel.state == "cleanup_pending",
                    SandboxLeaseModel.expires_at < datetime.now(),
                ),
            )
            .order_by(SandboxLeaseModel.gmt_modified, SandboxLeaseModel.id)
            .limit(batch_limit)
        ).all()
        return [(uuid, _connection(connection)) for uuid, connection in rows]


def _contains_connection(value: Any, uuid: str) -> bool:
    if isinstance(value, dict):
        return value.get("connection_uuid") == uuid or any(
            _contains_connection(item, uuid) for item in value.values()
        )
    return isinstance(value, list) and any(_contains_connection(item, uuid) for item in value)
