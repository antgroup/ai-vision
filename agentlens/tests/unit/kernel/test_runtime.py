import asyncio

import pytest
from sqlalchemy import text

from agentlens.kernel.runtime import Runtime


def _expire(session):
    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET expires_at = DATE_SUB(NOW(), INTERVAL 60 SECOND)
        WHERE lock_key='master'
    """)
    )
    session.commit()


@pytest.fixture
def settings(monkeypatch, mysql_url):
    monkeypatch.setenv("AGENTLENS_OB_URL", mysql_url)
    monkeypatch.setenv("AGENTLENS_TOKEN", "t")
    monkeypatch.setenv("AGENTLENS_HOSTNAME", "rt-host")
    monkeypatch.setenv("AGENTLENS_IP", "10.0.0.99")
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "x")
    monkeypatch.setenv("SIGNOZ_API_KEY", "x")
    monkeypatch.setenv("SIGNOZ_ORG_ID", "test-org")
    monkeypatch.setenv("ANTCOLLECTOR_ENDPOINT", "x")
    monkeypatch.setenv("ANTCOLLECTOR_NAMESPACE", "x")
    monkeypatch.setenv("ANTCOLLECTOR_JOB", "x")
    monkeypatch.setenv("ANTCOLLECTOR_TRACE_TENANT", "x")
    monkeypatch.setenv("HEARTBEAT_INTERVAL_SEC", "0.2")
    monkeypatch.setenv("LEASE_TTL_SEC", "1")
    monkeypatch.setenv("DISPATCH_LOOP_INTERVAL_SEC", "0.2")
    monkeypatch.setenv("RESCUE_LOOP_INTERVAL_SEC", "0.2")
    from agentlens.config import Settings as S

    return S()


@pytest.mark.asyncio
async def test_runtime_starts_heartbeat_and_acquires_master(session, settings):
    _expire(session)
    rt = Runtime(settings)
    await rt.start()
    await asyncio.sleep(0.6)
    assert rt.elector.is_leader is True
    assert rt.master is not None
    assert rt.master.cleanup is not None
    await rt.stop()


@pytest.mark.asyncio
async def test_runtime_does_not_start_task_cleanup_when_disabled(session, settings):
    _expire(session)
    settings.task_cleanup_enabled = False
    rt = Runtime(settings)
    await rt.start()
    await asyncio.sleep(0.6)

    assert rt.elector.is_leader is True
    assert rt.master is not None
    assert rt.master.cleanup is None

    await rt.stop()


@pytest.mark.asyncio
async def test_runtime_kills_master_when_role_lost(session, settings):
    _expire(session)
    rt = Runtime(settings)
    await rt.start()
    await asyncio.sleep(0.6)
    assert rt.master is not None

    # simulate leadership takeover by another instance
    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET holder_uuid='other/8.8.8.8',
            expires_at=DATE_ADD(NOW(), INTERVAL 60 SECOND)
        WHERE lock_key='master'
    """)
    )
    session.commit()
    await asyncio.sleep(0.6)
    assert rt.elector.is_leader is False
    assert rt.master is None
    await rt.stop()


@pytest.mark.asyncio
async def test_master_cleanup_waits_for_local_tasks_before_closing_client(settings):
    events = []
    release = asyncio.Event()

    async def loop_task():
        try:
            await asyncio.Event().wait()
        finally:
            events.append("loop-stopped")

    class _Dispatch:
        async def wait_local_tasks(self):
            await release.wait()
            events.append("local-finished")

    class _Client:
        def close(self):
            events.append("client-closed")

    task = asyncio.create_task(loop_task())
    task.cancel()
    runtime = Runtime(settings)
    cleanup = asyncio.create_task(runtime._finish_master_shutdown([task], _Dispatch(), _Client()))
    await asyncio.sleep(0)
    assert "client-closed" not in events

    release.set()
    await cleanup

    assert events[-2:] == ["local-finished", "client-closed"]
