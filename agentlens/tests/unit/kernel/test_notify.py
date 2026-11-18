import httpx
import pytest

from agentlens.kernel.notify import NotifyClient


def _mock_router(captured: list[dict]):
    def handler(req: httpx.Request) -> httpx.Response:
        captured.append({"url": str(req.url), "json": req.read().decode()})
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_push_execute_calls_worker_endpoint():
    captured: list[dict] = []
    async with NotifyClient(token="dev", port=8080, transport=_mock_router(captured)) as c:
        ok = await c.push_execute(worker_uuid="h1/1.1.1.1", task_uuid="t-1", fenced_token=7)
        assert ok is True
    assert captured[0]["url"].endswith("/internal/execute")
    assert "h1" not in captured[0]["url"]  # 用 IP 调,不用 hostname
    assert "1.1.1.1" in captured[0]["url"]


@pytest.mark.asyncio
async def test_push_execute_returns_false_on_error():
    bad = httpx.MockTransport(lambda req: httpx.Response(500))
    async with NotifyClient(token="dev", port=8080, transport=bad) as c:
        ok = await c.push_execute("h/1.1.1.1", "t-1", 1)
    assert ok is False


@pytest.mark.asyncio
async def test_notify_master_about_new_task():
    captured: list[dict] = []
    async with NotifyClient(token="dev", port=8080, transport=_mock_router(captured)) as c:
        ok = await c.notify_master(master_uuid="m/9.9.9.9", task_uuid="t-x")
    assert ok is True
    assert captured[0]["url"].endswith("/internal/notify")
    assert "9.9.9.9" in captured[0]["url"]
