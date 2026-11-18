from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class LeaseStatus:
    is_leader: bool
    holder_uuid: str
    fenced_token: int


class LeaderLeaseTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_master(self) -> LeaseStatus | None:
        row = (
            self.s.execute(
                text("""
            SELECT holder_uuid, fenced_token, expires_at
            FROM agentlens_leader_lease
            WHERE lock_key = 'master'
        """)
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        return LeaseStatus(
            is_leader=False,
            holder_uuid=row["holder_uuid"],
            fenced_token=row["fenced_token"],
        )

    def try_acquire_or_renew(self, me: str, ttl_sec: int) -> LeaseStatus:
        # Safety note: the two UPDATEs below are NOT atomic as a pair, but each
        # individual UPDATE is atomic and takes an X-lock on the row in MySQL/OB.
        # Concurrent callers serialize on the row lock, so only one can succeed
        # the acquire UPDATE per lease expiry cycle. This is safe under InnoDB
        # row-level locking but would need explicit SELECT FOR UPDATE if ported
        # to a DB without implicit write-locks on UPDATE...WHERE.
        # 1) 抢主：旧 master 过期
        acquired = self.s.execute(
            text("""
            UPDATE agentlens_leader_lease
            SET holder_uuid = :me,
                acquired_at  = NOW(),
                expires_at   = DATE_ADD(NOW(), INTERVAL :ttl SECOND),
                fenced_token = fenced_token + 1,
                gmt_modified = NOW(),
                modifier     = :me
            WHERE lock_key = 'master' AND expires_at < NOW()
        """),
            {"me": me, "ttl": ttl_sec},
        ).rowcount

        # 2) 续约：我已是 master
        renewed = self.s.execute(
            text("""
            UPDATE agentlens_leader_lease
            SET expires_at   = DATE_ADD(NOW(), INTERVAL :ttl SECOND),
                gmt_modified = NOW()
            WHERE lock_key = 'master' AND holder_uuid = :me
        """),
            {"me": me, "ttl": ttl_sec},
        ).rowcount

        self.s.commit()
        is_leader = acquired > 0 or renewed > 0
        cur = self.get_master()
        assert cur is not None
        return LeaseStatus(
            is_leader=is_leader and cur.holder_uuid == me,
            holder_uuid=cur.holder_uuid,
            fenced_token=cur.fenced_token,
        )
