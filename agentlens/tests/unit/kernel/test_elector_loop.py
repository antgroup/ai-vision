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
async def test_first_tick_acquires(session):
    _expire(session)
    table = LeaderLeaseTable(session)
    el = LeaderElectorLoop(table=table, me="A/1", ttl_sec=30, interval_sec=999)
    await el.tick()
    assert el.is_leader is True
    assert el.fenced_token >= 1


@pytest.mark.asyncio
async def test_loses_when_other_holds(session):
    _expire(session)
    other = LeaderLeaseTable(session)
    other.try_acquire_or_renew("B/2", ttl_sec=30)

    me = LeaderElectorLoop(table=LeaderLeaseTable(session), me="A/1", ttl_sec=30, interval_sec=999)
    await me.tick()
    assert me.is_leader is False


@pytest.mark.asyncio
async def test_role_change_callback_fires(session):
    _expire(session)
    table = LeaderLeaseTable(session)
    el = LeaderElectorLoop(table=table, me="A/1", ttl_sec=30, interval_sec=999)
    events: list[bool] = []
    el.on_role_change(lambda is_leader: events.append(is_leader))

    await el.tick()  # 抢到主
    assert events == [True]

    # 强行让别人接管
    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET holder_uuid='B/2', expires_at=DATE_ADD(NOW(), INTERVAL 30 SECOND)
        WHERE lock_key='master'
    """)
    )
    session.commit()

    await el.tick()
    assert events == [True, False]
