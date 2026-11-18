from sqlalchemy import text

from agentlens.infra.ob.tables.leader_lease_table import LeaderLeaseTable


def _force_expire(session):
    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET expires_at = DATE_SUB(NOW(), INTERVAL 60 SECOND)
        WHERE lock_key='master'
    """)
    )
    session.commit()


def test_acquire_when_expired_bumps_fenced_token(session):
    t = LeaderLeaseTable(session)
    _force_expire(session)
    res = t.try_acquire_or_renew(me="node-1/1.1.1.1", ttl_sec=30)
    assert res.is_leader
    assert res.fenced_token >= 1
    cur = t.get_master()
    assert cur.holder_uuid == "node-1/1.1.1.1"


def test_renew_when_already_leader_keeps_token(session):
    t = LeaderLeaseTable(session)
    _force_expire(session)
    r1 = t.try_acquire_or_renew("node-1/1.1.1.1", ttl_sec=30)
    r2 = t.try_acquire_or_renew("node-1/1.1.1.1", ttl_sec=30)
    assert r2.is_leader
    assert r2.fenced_token == r1.fenced_token


def test_acquire_fails_if_other_holds_unexpired(session):
    t = LeaderLeaseTable(session)
    _force_expire(session)
    t.try_acquire_or_renew("node-A/a", ttl_sec=30)
    res = t.try_acquire_or_renew("node-B/b", ttl_sec=30)
    assert res.is_leader is False
    cur = t.get_master()
    assert cur.holder_uuid == "node-A/a"
