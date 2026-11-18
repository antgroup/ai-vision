from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agentlens.domain.heartbeat import Heartbeat
from agentlens.infra.ob.models import HeartbeatModel


def _to_domain(m: HeartbeatModel) -> Heartbeat:
    return Heartbeat(
        uuid=m.uuid,
        hostname=m.hostname,
        ip=m.ip,
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
    )


class HeartbeatTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def upsert(self, hostname: str, ip: str) -> None:
        uuid = f"{hostname}/{ip}"
        # MySQL INSERT ... ON DUPLICATE KEY UPDATE
        self.s.execute(
            text("""
            INSERT INTO agentlens_heartbeat (uuid, hostname, ip, gmt_create, gmt_modified)
            VALUES (:uuid, :hn, :ip, NOW(), NOW())
            ON DUPLICATE KEY UPDATE gmt_modified = NOW()
        """),
            {"uuid": uuid, "hn": hostname, "ip": ip},
        )
        self.s.commit()

    def get(self, uuid: str) -> Heartbeat | None:
        m = self.s.scalar(select(HeartbeatModel).where(HeartbeatModel.uuid == uuid))
        return _to_domain(m) if m else None

    def list_alive(self, threshold_sec: int) -> list[Heartbeat]:
        self.s.commit()
        rows = (
            self.s.execute(
                text("""
            SELECT * FROM agentlens_heartbeat
            WHERE gmt_modified > DATE_SUB(NOW(), INTERVAL :sec SECOND)
        """),
                {"sec": threshold_sec},
            )
            .mappings()
            .all()
        )
        return [
            Heartbeat(
                uuid=r["uuid"],
                hostname=r["hostname"],
                ip=r["ip"],
                gmt_create=r["gmt_create"],
                gmt_modified=r["gmt_modified"],
            )
            for r in rows
        ]
