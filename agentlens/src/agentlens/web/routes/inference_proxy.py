from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse

from agentlens.service.inference.inference_proxy import (
    MAX_REQUEST_BODY_BYTES,
    InferenceProxyError,
    InferenceProxyService,
)
from agentlens.web.deps import get_inference_proxy
from agentlens.web.openai import openai_error_response

router = APIRouter(tags=["inference-proxy"])
ProxyDep = Annotated[InferenceProxyService, Depends(get_inference_proxy)]

_PATHS = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
)


async def _read_limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                raise InferenceProxyError.request_too_large()
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_BODY_BYTES:
            raise InferenceProxyError.request_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _error_response(error: InferenceProxyError):
    return openai_error_response(
        error.status_code,
        error.message,
        error.error_type,
        error.code,
        param=error.param,
    )


def _response_headers(response, *, stream: bool) -> dict[str, str]:
    headers: dict[str, str] = {}
    content_type = response.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    if stream:
        cache_control = response.headers.get("cache-control")
        if cache_control:
            headers["Cache-Control"] = cache_control
    return headers


async def _proxy(path: str, request: Request, proxy: ProxyDep):
    try:
        body = await _read_limited_body(request)
        prepared = proxy.prepare(body)
    except InferenceProxyError as error:
        proxy.log_local_error(path=path, code=error.code)
        return _error_response(error)

    query = request.url.query
    accept = request.headers.get("accept")
    if prepared.stream:
        try:
            opened, started = await proxy.open_stream(
                prepared,
                path=path,
                query=query,
                accept=accept,
            )
        except InferenceProxyError as error:
            proxy.log_local_error(
                path=path,
                code=error.code,
                model=prepared.model,
                stream=True,
            )
            return _error_response(error)
        body_iterator: AsyncIterator[bytes] = proxy.iter_stream(
            opened,
            prepared,
            path=path,
            started=started,
        )
        return StreamingResponse(
            body_iterator,
            status_code=opened.response.status_code,
            headers=_response_headers(opened.response, stream=True),
        )

    try:
        response = await proxy.forward(
            prepared,
            path=path,
            query=query,
            accept=accept,
        )
    except InferenceProxyError as error:
        proxy.log_local_error(
            path=path,
            code=error.code,
            model=prepared.model,
            stream=False,
        )
        return _error_response(error)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=_response_headers(response, stream=False),
    )


@router.post(_PATHS[0])
async def chat_completions(request: Request, proxy: ProxyDep):
    return await _proxy(_PATHS[0], request, proxy)


@router.post(_PATHS[1])
async def completions(request: Request, proxy: ProxyDep):
    return await _proxy(_PATHS[1], request, proxy)


@router.post(_PATHS[2])
async def responses(request: Request, proxy: ProxyDep):
    return await _proxy(_PATHS[2], request, proxy)
