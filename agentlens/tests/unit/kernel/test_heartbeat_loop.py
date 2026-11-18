import asyncio

import pytest

from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.kernel.heartbeat import HeartbeatLoop


@pytest.mark.asyncio
async def test_one_tick_writes_heartbeat(session):
    table = HeartbeatTable(session)
    loop = HeartbeatLoop(table=table, hostname="h1", ip="1.1.1.1", interval_sec=999)
    await loop.tick()  # 直接跑一次 tick，避免等定时
    assert table.get("h1/1.1.1.1") is not None


@pytest.mark.asyncio
async def test_run_loops_until_cancel(engine):
    # Use a dedicated session — the loop's to_thread ticks can overlap,
    # so the shared conftest session isn't safe across threads.
    from agentlens.infra.ob.engine import make_session_factory

    sf = make_session_factory(engine)
    s = sf()
    try:
        table = HeartbeatTable(s)
        loop = HeartbeatLoop(table=table, hostname="h2", ip="2.2.2.2", interval_sec=0.1)
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        s2 = sf()
        assert HeartbeatTable(s2).get("h2/2.2.2.2") is not None
        s2.close()
    finally:
        s.close()
