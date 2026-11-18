from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx


class InferenceProxyHttpClient:
    """Async HTTP transport for the development inference proxy."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: Mapping[str, str],
    ) -> httpx.Response:
        request = self._build_request(url, content=content, headers=headers)
        return await self._client.send(request)

    @asynccontextmanager
    async def stream(
        self,
        url: str,
        *,
        content: bytes,
        headers: Mapping[str, str],
    ) -> AsyncIterator[httpx.Response]:
        request = self._build_request(url, content=content, headers=headers)
        response = await self._client.send(request, stream=True)
        try:
            yield response
        finally:
            await response.aclose()

    def _build_request(
        self,
        url: str,
        *,
        content: bytes,
        headers: Mapping[str, str],
    ) -> httpx.Request:
        request = self._client.build_request("POST", url, content=content, headers=headers)
        for header in ("accept-encoding", "connection", "user-agent"):
            request.headers.pop(header, None)
        if "Accept" not in headers:
            request.headers.pop("accept", None)
        return request

    async def aclose(self) -> None:
        await self._client.aclose()
