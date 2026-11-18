from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from agentlens.config import Settings
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.web.deps import SessionDep, get_heartbeat_table, get_settings
from agentlens.web.schemas.cluster import InstanceOut, LeaderOut
from agentlens.web.schemas.common import ListResponse

router = APIRouter(prefix="/api/v1/cluster", tags=["cluster"])
HbDep = Annotated[HeartbeatTable, Depends(get_heartbeat_table)]


@router.get("/instances", response_model=ListResponse[InstanceOut])
def list_instances(
    table: HbDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ListResponse[InstanceOut]:
    rows = table.list_alive(threshold_sec=settings.lease_ttl_sec)
    return ListResponse(
        items=[
            InstanceOut(
                uuid=h.uuid,
                hostname=h.hostname,
                ip=h.ip,
                gmt_create=h.gmt_create,
                gmt_modified=h.gmt_modified,
            )
            for h in rows
        ],
        total=len(rows),
    )


@router.get("/leader", response_model=LeaderOut)
def leader_info(session: SessionDep) -> LeaderOut:
    row = (
        session.execute(
            text("""
        SELECT holder_uuid, acquired_at, expires_at, fenced_token
        FROM agentlens_leader_lease WHERE lock_key='master'
    """)
        )
        .mappings()
        .first()
    )
    return LeaderOut(**row)
