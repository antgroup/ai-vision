import time

from sqlalchemy import text

from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable


def test_upsert_creates_then_refreshes(session):
    t = HeartbeatTable(session)
    t.upsert(hostname="node-1", ip="10.0.0.1")
    first = t.get("node-1/10.0.0.1")
    assert first is not None

    time.sleep(1.1)
    t.upsert(hostname="node-1", ip="10.0.0.1")
    second = t.get("node-1/10.0.0.1")
    assert second.gmt_modified > first.gmt_modified
    assert second.gmt_create == first.gmt_create  # 不变


def test_list_alive_filters_by_threshold(session):
    t = HeartbeatTable(session)
    t.upsert(hostname="alive", ip="1.1.1.1")
    t.upsert(hostname="dead", ip="2.2.2.2")
    # 手动把 dead 的 gmt_modified 推到很久以前
    session.execute(
        text("""
        UPDATE agentlens_heartbeat
        SET gmt_modified = DATE_SUB(NOW(), INTERVAL 5 MINUTE)
        WHERE uuid = 'dead/2.2.2.2'
    """)
    )
    session.commit()
    alive = t.list_alive(threshold_sec=30)
    uuids = {h.uuid for h in alive}
    assert "alive/1.1.1.1" in uuids
    assert "dead/2.2.2.2" not in uuids
