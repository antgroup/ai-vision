from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.model_connection import ModelConnection
from agentlens.domain.model_validation import DiagnosticValidation
from agentlens.infra.ob.models import ModelConnectionModel


def _to_domain(m: ModelConnectionModel) -> ModelConnection:
    return ModelConnection(
        uuid=m.uuid,
        space_uuid=m.space_uuid,
        name=m.name,
        endpoint=m.endpoint,
        model_name=m.model_name,
        api_key=m.api_key or "",
        status=ModelConnectionStatus(m.status),
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
        creator=m.creator,
        modifier=m.modifier,
        diagnostic_validation=m.diagnostic_validation,
        config_revision=m.config_revision,
    )


class ModelConnectionTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def insert(self, connection: ModelConnection) -> None:
        now = datetime.now()
        self.s.add(
            ModelConnectionModel(
                uuid=connection.uuid,
                space_uuid=connection.space_uuid,
                name=connection.name,
                endpoint=connection.endpoint,
                model_name=connection.model_name,
                api_key=connection.api_key,
                status=connection.status.value,
                gmt_create=now,
                gmt_modified=now,
                creator=connection.creator,
                modifier=connection.modifier,
            )
        )
        self.s.commit()

    def get_by_uuid(self, uuid: str) -> ModelConnection | None:
        model = self.s.scalar(select(ModelConnectionModel).where(ModelConnectionModel.uuid == uuid))
        return _to_domain(model) if model else None

    def get_by_uuid_for_update(self, uuid: str) -> ModelConnection | None:
        model = self.s.scalar(
            select(ModelConnectionModel).where(ModelConnectionModel.uuid == uuid).with_for_update()
        )
        return _to_domain(model) if model else None

    def get_in_space(self, uuid: str, space_uuid: str) -> ModelConnection | None:
        model = self.s.scalar(
            select(ModelConnectionModel).where(
                ModelConnectionModel.uuid == uuid,
                ModelConnectionModel.space_uuid == space_uuid,
            )
        )
        return _to_domain(model) if model else None

    def get_in_space_for_update(self, uuid: str, space_uuid: str) -> ModelConnection | None:
        model = self.s.scalar(
            select(ModelConnectionModel)
            .where(
                ModelConnectionModel.uuid == uuid,
                ModelConnectionModel.space_uuid == space_uuid,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _to_domain(model) if model else None

    def list(self, space_uuid: str) -> list[ModelConnection]:
        stmt = (
            select(ModelConnectionModel)
            .where(ModelConnectionModel.space_uuid == space_uuid)
            .order_by(ModelConnectionModel.gmt_create.desc(), ModelConnectionModel.id.desc())
        )
        return [_to_domain(model) for model in self.s.scalars(stmt)]

    def update_by_uuid(
        self,
        uuid: str,
        fields: dict[str, Any],
        modifier: str | None = None,
    ) -> bool:
        if {"endpoint", "model_name", "api_key"}.intersection(fields):
            fields = {
                **fields,
                "diagnostic_validation": None,
                "config_revision": ModelConnectionModel.config_revision + 1,
                "status": ModelConnectionStatus.UNTESTED.value,
            }
        if modifier is not None:
            fields = {**fields, "modifier": modifier}
        fields = {**fields, "gmt_modified": datetime.now()}
        result = self.s.execute(
            update(ModelConnectionModel).where(ModelConnectionModel.uuid == uuid).values(**fields)
        )
        self.s.commit()
        return result.rowcount > 0

    def validation_snapshot(self, uuid: str, space_uuid: str) -> ModelConnection | None:
        snapshot = self.get_in_space(uuid, space_uuid)
        # Do not retain a database transaction or row lock across provider I/O.
        self.s.rollback()
        return snapshot

    def connectivity_snapshot(self, uuid: str) -> ModelConnection | None:
        snapshot = self.get_by_uuid(uuid)
        # A provider request must not retain the read transaction or its locks.
        self.s.rollback()
        return snapshot

    def save_test_status(self, snapshot: ModelConnection, status: ModelConnectionStatus) -> bool:
        result = self.s.execute(
            update(ModelConnectionModel)
            .where(
                ModelConnectionModel.uuid == snapshot.uuid,
                ModelConnectionModel.space_uuid == snapshot.space_uuid,
                ModelConnectionModel.config_revision == snapshot.config_revision,
                ModelConnectionModel.endpoint == snapshot.endpoint,
                ModelConnectionModel.model_name == snapshot.model_name,
                ModelConnectionModel.api_key == snapshot.api_key,
            )
            .values(status=status.value, gmt_modified=datetime.now())
        )
        self.s.commit()
        return result.rowcount > 0

    def save_validation(
        self, snapshot: ModelConnection, report: dict, status: ModelConnectionStatus
    ) -> bool:
        safe_report = DiagnosticValidation.model_validate(report).model_dump(mode="json")
        result = self.s.execute(
            update(ModelConnectionModel)
            .where(
                ModelConnectionModel.uuid == snapshot.uuid,
                ModelConnectionModel.space_uuid == snapshot.space_uuid,
                ModelConnectionModel.config_revision == snapshot.config_revision,
                ModelConnectionModel.endpoint == snapshot.endpoint,
                ModelConnectionModel.model_name == snapshot.model_name,
                ModelConnectionModel.api_key == snapshot.api_key,
            )
            .values(
                diagnostic_validation=safe_report, status=status.value, gmt_modified=datetime.now()
            )
        )
        self.s.commit()
        return result.rowcount > 0

    def delete_by_uuid(self, uuid: str) -> bool:
        result = self.s.execute(
            delete(ModelConnectionModel).where(ModelConnectionModel.uuid == uuid)
        )
        self.s.commit()
        return result.rowcount > 0
