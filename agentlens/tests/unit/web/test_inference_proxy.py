from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.infra.inference_proxy import InferenceProxyHttpClient
from agentlens.service.inference.inference_proxy import InferenceProxyService
from agentlens.web.auth import TokenAuthMiddleware
from agentlens.web.routes.inference_proxy import router

TOKEN = "secret-token"


class _ChunkStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"data: one\n\n"
        yield b"data: two\n\n"


class _TimeoutStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"data: one\n\n"
        raise httpx.ReadTimeout("late read timeout")


@pytest.fixture
def app():
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-drop": "secret"},
            content=b'{"ok":true}',
            request=request,
        )

    fastapi_app = FastAPI()
    fastapi_app.add_middleware(TokenAuthMiddleware, expected_token=TOKEN)
    fastapi_app.state.inference_proxy = InferenceProxyService(
        InferenceProxyHttpClient(transport=httpx.MockTransport(handler))
    )
    fastapi_app.include_router(router)
    fastapi_app.state.captured = captured
    yield fastapi_app
    asyncio.run(fastapi_app.state.inference_proxy.aclose())


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_chat_completion_forwards_body_query_and_safe_headers(client, app):
    payload = {
        "model": "Qwen3.8-27B-L20",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "future_field": {"keep": True},
    }
    response = client.post(
        "/v1/chat/completions?trace_id=debug-1",
        json=payload,
        headers={
            **_auth(),
            "Accept": "application/json",
            "Cookie": "secret-cookie",
            "x-antmonitor-token": "wrong-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["content-type"] == "application/json"
    assert "x-drop" not in response.headers

    upstream = app.state.captured[0]
    assert str(upstream.url) == ("http://33.153.162.225:30000/v1/chat/completions?trace_id=debug-1")
    assert json.loads(upstream.content) == payload
    assert upstream.headers["content-type"] == "application/json"
    assert upstream.headers["accept"] == "application/json"
    assert "authorization" not in upstream.headers
    assert "cookie" not in upstream.headers
    assert "x-antmonitor-token" not in upstream.headers
    assert "accept-encoding" not in upstream.headers
    assert "connection" not in upstream.headers
    assert "user-agent" not in upstream.headers


def test_all_protocol_paths_are_forwarded_without_translation(client, app):
    for path, suffix in (
        ("/v1/chat/completions", "/chat/completions"),
        ("/v1/completions", "/completions"),
        ("/v1/responses", "/responses"),
    ):
        response = client.post(path, json={"model": "Qwen3.5-35B-A3B-FP8"}, headers=_auth())
        assert response.status_code == 200
        assert app.state.captured[-1].url.path == f"/v1{suffix}"


def test_stream_response_is_forwarded_as_sse():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
            content=_ChunkStream(),
            request=request,
        )

    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware, expected_token=TOKEN)
    proxy = InferenceProxyService(InferenceProxyHttpClient(transport=httpx.MockTransport(handler)))
    app.state.inference_proxy = proxy
    app.include_router(router)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/responses",
                json={"model": "Qwen3.8-27B-L20", "stream": True},
                headers={**_auth(), "Accept": "text/event-stream"},
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.content == b"data: one\n\ndata: two\n\n"
    finally:
        asyncio.run(proxy.aclose())


def test_stream_read_timeout_after_headers_closes_stream_without_error_event():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_TimeoutStream(),
            request=request,
        )

    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware, expected_token=TOKEN)
    proxy = InferenceProxyService(InferenceProxyHttpClient(transport=httpx.MockTransport(handler)))
    app.state.inference_proxy = proxy
    app.include_router(router)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "Qwen3.8-27B-L20", "stream": True},
                headers=_auth(),
            )
        assert response.status_code == 200
        assert response.content == b"data: one\n\n"
        assert b"upstream_timeout" not in response.content
    finally:
        asyncio.run(proxy.aclose())


@pytest.mark.parametrize(
    ("body", "status_code", "error_code"),
    [
        (b"not-json", 400, "invalid_request"),
        (b"{}", 400, "invalid_request"),
        (b'{"model":"missing"}', 404, "model_not_found"),
        (b"x" * (16 * 1024 * 1024 + 1), 413, "request_too_large"),
    ],
)
def test_local_validation_errors_use_openai_shape(client, body, status_code, error_code):
    response = client.post("/v1/chat/completions", content=body, headers=_auth())
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code


@pytest.mark.parametrize(
    "headers", [{}, {"x-antmonitor-token": TOKEN}, {"Authorization": "Bearer wrong"}]
)
def test_inference_paths_require_bearer_auth(client, headers):
    response = client.post(
        "/v1/chat/completions", json={"model": "Qwen3.8-27B-L20"}, headers=headers
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_upstream_error_is_not_wrapped(client, app):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"content-type": "application/json"},
            content=b'{"error":{"message":"busy","type":"rate_limit_error"}}',
            request=request,
        )

    asyncio.run(app.state.inference_proxy.aclose())
    app.state.inference_proxy = InferenceProxyService(
        InferenceProxyHttpClient(transport=httpx.MockTransport(handler))
    )
    response = client.post(
        "/v1/chat/completions", json={"model": "Qwen3.8-27B-L20"}, headers=_auth()
    )
    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"


@pytest.mark.parametrize(
    ("exception", "status_code", "error_code"),
    [
        (httpx.ConnectError("connection refused"), 502, "upstream_unavailable"),
        (httpx.ReadTimeout("read timeout"), 504, "upstream_timeout"),
    ],
)
def test_upstream_transport_errors_are_safe(client, app, exception, status_code, error_code):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    asyncio.run(app.state.inference_proxy.aclose())
    app.state.inference_proxy = InferenceProxyService(
        InferenceProxyHttpClient(transport=httpx.MockTransport(handler))
    )
    response = client.post(
        "/v1/chat/completions", json={"model": "Qwen3.8-27B-L20"}, headers=_auth()
    )
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code
