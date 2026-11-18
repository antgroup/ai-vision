import pytest
from sqlalchemy import text

from agentlens.infra.ob.tables.leader_lease_table import LeaderLeaseTable
from agentlens.kernel.elector import LeaderElectorLoop


def _expire(session):
    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET expires_at = DATE_SUB(NOW(), INTERVAL 60 SECOND)
        WHERE lock_key='master'
    """)
    )
    session.commit()


@pytest.mark.asyncio
async def test_two_electors_only_one_is_leader(session):
    _expire(session)
    e1 = LeaderElectorLoop(LeaderLeaseTable(session), me="A/1.1.1.1", ttl_sec=30, interval_sec=999)
    e2 = LeaderElectorLoop(LeaderLeaseTable(session), me="B/2.2.2.2", ttl_sec=30, interval_sec=999)
    await e1.tick()
    await e2.tick()
    assert (e1.is_leader, e2.is_leader) == (True, False)


@pytest.mark.asyncio
async def test_fenced_token_monotonically_increases_on_failover(session):
    _expire(session)
    e1 = LeaderElectorLoop(LeaderLeaseTable(session), me="A/1.1.1.1", ttl_sec=1, interval_sec=999)
    await e1.tick()
    t1 = e1.fenced_token

    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET expires_at = DATE_SUB(NOW(), INTERVAL 5 SECOND)
        WHERE lock_key='master'
    """)
    )
    session.commit()

    e2 = LeaderElectorLoop(LeaderLeaseTable(session), me="B/2.2.2.2", ttl_sec=30, interval_sec=999)
    await e2.tick()
    assert e2.is_leader is True
    assert e2.fenced_token > t1
