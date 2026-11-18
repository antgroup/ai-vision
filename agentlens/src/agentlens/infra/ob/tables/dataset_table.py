from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, select, update
from sqlalchemy.orm import Session

from agentlens.domain.dataset import Dataset
from agentlens.domain.enums import DatasetType
from agentlens.infra.ob.models import DatasetModel


def _to_domain(m: DatasetModel) -> Dataset:
    return Dataset(
        uuid=m.uuid,
        space_uuid=m.space_uuid,
        agent_uuid=m.agent_uuid,
        name=m.name,
        description=m.description or "",
        type=DatasetType(m.type),
        spec=m.spec or {},
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
        creator=m.creator,
        modifier=m.modifier,
    )


class DatasetTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def insert(self, d: Dataset) -> None:
        now = datetime.now()
        m = DatasetModel(
            uuid=d.uuid,
            space_uuid=d.space_uuid,
            agent_uuid=d.agent_uuid,
            name=d.name,
            description=d.description,
            type=d.type.value,
            spec=d.spec,
            gmt_create=now,
            gmt_modified=now,
            creator=d.creator,
            modifier=d.modifier,
        )
        self.s.add(m)
        self.s.commit()

    def get_by_uuid(self, uuid: str) -> Dataset | None:
        m = self.s.scalar(select(DatasetModel).where(DatasetModel.uuid == uuid))
        return _to_domain(m) if m else None

    def get_by_uuid_for_update(self, uuid: str) -> Dataset | None:
        stmt = (
            select(DatasetModel)
            .where(DatasetModel.uuid == uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        m = self.s.scalar(stmt)
        return _to_domain(m) if m else None

    def list(
        self,
        space_uuid: str | None = None,
        agent_uuid: str | None = None,
        *,
        space_uuids: Sequence[str] | None = None,
    ) -> list[Dataset]:
        stmt = select(DatasetModel)
        ordered_spaces: tuple[str, ...] = ()
        if space_uuids is not None:
            ordered_spaces = tuple(dict.fromkeys(space_uuids))
            stmt = stmt.where(DatasetModel.space_uuid.in_(ordered_spaces))
        elif space_uuid:
            stmt = stmt.where(DatasetModel.space_uuid == space_uuid)
        if agent_uuid is not None:
            stmt = stmt.where(DatasetModel.agent_uuid == agent_uuid)
        if ordered_spaces:
            stmt = stmt.order_by(
                case(
                    {value: index for index, value in enumerate(ordered_spaces)},
                    value=DatasetModel.space_uuid,
                    else_=len(ordered_spaces),
                ),
                DatasetModel.id,
            )
        else:
            stmt = stmt.order_by(DatasetModel.id)
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def update_by_uuid(
        self,
        uuid: str,
        fields: dict[str, Any],
        modifier: str | None = None,
    ) -> bool:
        if modifier is not None:
            fields = {**fields, "modifier": modifier}
        fields = {**fields, "gmt_modified": datetime.now()}
        r = self.s.execute(update(DatasetModel).where(DatasetModel.uuid == uuid).values(**fields))
        self.s.commit()
        return r.rowcount > 0

    def delete_by_uuid(self, uuid: str) -> bool:
        r = self.s.execute(delete(DatasetModel).where(DatasetModel.uuid == uuid))
        self.s.commit()
        return r.rowcount > 0
