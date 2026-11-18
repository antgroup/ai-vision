from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, select, update
from sqlalchemy.orm import Session

from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import DiagnoserMode, DiagnoserType
from agentlens.infra.ob.models import DiagnoserModel


def _to_domain(m: DiagnoserModel) -> Diagnoser:
    return Diagnoser(
        uuid=m.uuid,
        space_uuid=m.space_uuid,
        agent_uuid=m.agent_uuid or "",
        name=m.name,
        description=m.description or "",
        type=DiagnoserType(m.type),
        mode=DiagnoserMode(m.mode),
        spec=m.spec or {},
        version=m.version,
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
        creator=m.creator,
        modifier=m.modifier,
    )


class DiagnoserTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def insert(self, d: Diagnoser) -> None:
        now = datetime.now()
        m = DiagnoserModel(
            uuid=d.uuid,
            space_uuid=d.space_uuid,
            agent_uuid=d.agent_uuid,
            name=d.name,
            description=d.description,
            type=d.type.value,
            mode=d.mode.value,
            spec=d.spec,
            version=d.version,
            gmt_create=now,
            gmt_modified=now,
            creator=d.creator,
            modifier=d.modifier,
        )
        self.s.add(m)
        self.s.commit()

    def rollback(self) -> None:
        """Discard the failed statement so the session can keep serving reads."""
        self.s.rollback()

    def get_by_uuid(self, uuid: str) -> Diagnoser | None:
        m = self.s.scalar(select(DiagnoserModel).where(DiagnoserModel.uuid == uuid))
        return _to_domain(m) if m else None

    def get_by_uuid_for_update(self, uuid: str) -> Diagnoser | None:
        stmt = (
            select(DiagnoserModel)
            .where(DiagnoserModel.uuid == uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        m = self.s.scalar(stmt)
        return _to_domain(m) if m else None

    def list_exact_scope(self, space_uuid: str, agent_uuid: str) -> list[Diagnoser]:
        stmt = (
            select(DiagnoserModel)
            .where(
                DiagnoserModel.space_uuid == space_uuid,
                DiagnoserModel.agent_uuid == agent_uuid,
            )
            .order_by(DiagnoserModel.id)
        )
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def list_exact_scope_for_update(
        self,
        space_uuid: str,
        agent_uuid: str,
    ) -> list[Diagnoser]:
        stmt = (
            select(DiagnoserModel)
            .where(
                DiagnoserModel.space_uuid == space_uuid,
                DiagnoserModel.agent_uuid == agent_uuid,
            )
            .order_by(DiagnoserModel.id)
            .with_for_update()
        )
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def list(
        self,
        space_uuid: str | None = None,
        type: str | None = None,
        agent_uuid: str | None = None,
        *,
        space_uuids: Sequence[str] | None = None,
    ) -> list[Diagnoser]:
        stmt = select(DiagnoserModel)
        ordered_spaces: tuple[str, ...] = ()
        if space_uuids is not None:
            ordered_spaces = tuple(dict.fromkeys(space_uuids))
            stmt = stmt.where(DiagnoserModel.space_uuid.in_(ordered_spaces))
        elif space_uuid:
            stmt = stmt.where(DiagnoserModel.space_uuid == space_uuid)
        if agent_uuid is not None:
            stmt = stmt.where(DiagnoserModel.agent_uuid == agent_uuid)
        if type:
            stmt = stmt.where(DiagnoserModel.type == type)
        if ordered_spaces:
            stmt = stmt.order_by(
                case(
                    {value: index for index, value in enumerate(ordered_spaces)},
                    value=DiagnoserModel.space_uuid,
                    else_=len(ordered_spaces),
                ),
                DiagnoserModel.id,
            )
        else:
            stmt = stmt.order_by(DiagnoserModel.id)
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
        r = self.s.execute(
            update(DiagnoserModel).where(DiagnoserModel.uuid == uuid).values(**fields)
        )
        self.s.commit()
        return r.rowcount > 0

    def delete_by_uuid(self, uuid: str) -> bool:
        r = self.s.execute(delete(DiagnoserModel).where(DiagnoserModel.uuid == uuid))
        self.s.commit()
        return r.rowcount > 0

    def delete_by_scope(self, space_uuid: str, agent_uuid: str) -> int:
        r = self.s.execute(
            delete(DiagnoserModel).where(
                DiagnoserModel.space_uuid == space_uuid,
                DiagnoserModel.agent_uuid == agent_uuid,
            )
        )
        self.s.commit()
        return int(r.rowcount or 0)
