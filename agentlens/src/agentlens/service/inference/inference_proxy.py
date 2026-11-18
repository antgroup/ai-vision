from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from agentlens.infra.inference_proxy import InferenceProxyHttpClient
from agentlens.logging import get_logger

MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024

# The values are intentionally source-controlled.  Keep each value as a list so
# future failover can be added without changing the route contract.
MODEL_ENDPOINTS: dict[str, list[str]] = {
    "Qwen3.5-35B-A3B-FP8": ["http://33.164.253.149:30000/v1"],
    "Qwen3.8-27B-L20": ["http://33.153.162.225:30000/v1"],
    "Qwen3.8-27B-H20": ["http://33.184.122.11:30010/v1"],
}

_UPSTREAM_PATHS = {
    "/v1/chat/completions": "/chat/completions",
    "/v1/completions": "/completions",
    "/v1/responses": "/responses",
}


class InferenceProxyError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        error_type: str,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.error_type = error_type
        self.param = param

    @classmethod
    def invalid_request(cls, message: str, *, param: str | None = None) -> InferenceProxyError:
        return cls(
            400,
            "invalid_request",
            message,
            error_type="invalid_request_error",
            param=param,
        )

    @classmethod
    def model_not_found(cls) -> InferenceProxyError:
        return cls(
            404,
            "model_not_found",
            "The requested model is not configured.",
            error_type="invalid_request_error",
            param="model",
        )

    @classmethod
    def request_too_large(cls) -> InferenceProxyError:
        return cls(
            413,
            "request_too_large",
            "Request body exceeds the 16 MiB limit.",
            error_type="invalid_request_error",
        )

    @classmethod
    def upstream_unavailable(cls) -> InferenceProxyError:
        return cls(
            502,
            "upstream_unavailable",
            "Unable to connect to the upstream inference engine.",
            error_type="server_error",
        )

    @classmethod
    def upstream_timeout(cls) -> InferenceProxyError:
        return cls(
            504,
            "upstream_timeout",
            "The upstream inference engine timed out.",
            error_type="server_error",
        )


@dataclass(frozen=True)
class PreparedInferenceRequest:
    model: str
    stream: bool
    body: bytes


@dataclass
class OpenedInferenceStream:
    response: httpx.Response
    context: Any


class InferenceProxyService:
    def __init__(
        self,
        http_client: InferenceProxyHttpClient | None = None,
        *,
        model_endpoints: Mapping[str, list[str]] | None = None,
    ) -> None:
        self.http_client = http_client or InferenceProxyHttpClient()
        self.model_endpoints = dict(model_endpoints or MODEL_ENDPOINTS)
        self._log = get_logger("inference-proxy")

    async def aclose(self) -> None:
        await self.http_client.aclose()

    def prepare(self, body: bytes) -> PreparedInferenceRequest:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise InferenceProxyError.invalid_request("Request body must be valid JSON.") from None
        if not isinstance(payload, dict):
            raise InferenceProxyError.invalid_request("Request body must be a JSON object.")

        model = payload.get("model")
        if not isinstance(model, str) or not model.strip():
            raise InferenceProxyError.invalid_request(
                "The `model` field must be a non-empty string.",
                param="model",
            )
        if not self._first_endpoint(model):
            raise InferenceProxyError.model_not_found()
        return PreparedInferenceRequest(
            model=model, stream=payload.get("stream") is True, body=body
        )

    def _first_endpoint(self, model: str) -> str | None:
        endpoints = self.model_endpoints.get(model) or []
        return endpoints[0] if endpoints else None

    def _url(self, model: str, path: str, query: str) -> str:
        upstream_path = _UPSTREAM_PATHS[path]
        endpoint = self._first_endpoint(model)
        if endpoint is None:
            raise InferenceProxyError.model_not_found()
        url = f"{endpoint.rstrip('/')}{upstream_path}"
        return f"{url}?{query}" if query else url

    @staticmethod
    def _headers(accept: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if accept:
            headers["Accept"] = accept
        return headers

    async def forward(
        self,
        request: PreparedInferenceRequest,
        *,
        path: str,
        query: str,
        accept: str | None,
    ) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = await self.http_client.post(
                self._url(request.model, path, query),
                content=request.body,
                headers=self._headers(accept),
            )
        except httpx.ConnectTimeout:
            raise InferenceProxyError.upstream_unavailable() from None
        except (httpx.ConnectError, httpx.NetworkError, httpx.ProtocolError):
            raise InferenceProxyError.upstream_unavailable() from None
        except httpx.TimeoutException:
            raise InferenceProxyError.upstream_timeout() from None
        except httpx.HTTPError:
            raise InferenceProxyError.upstream_unavailable() from None
        self._log_completed(request, path, response.status_code, started)
        return response

    async def open_stream(
        self,
        request: PreparedInferenceRequest,
        *,
        path: str,
        query: str,
        accept: str | None,
    ) -> tuple[OpenedInferenceStream, float]:
        started = time.perf_counter()
        context = self.http_client.stream(
            self._url(request.model, path, query),
            content=request.body,
            headers=self._headers(accept),
        )
        try:
            response = await context.__aenter__()
        except httpx.ConnectTimeout:
            raise InferenceProxyError.upstream_unavailable() from None
        except (httpx.ConnectError, httpx.NetworkError, httpx.ProtocolError):
            raise InferenceProxyError.upstream_unavailable() from None
        except httpx.TimeoutException:
            raise InferenceProxyError.upstream_timeout() from None
        except httpx.HTTPError:
            raise InferenceProxyError.upstream_unavailable() from None
        return OpenedInferenceStream(response=response, context=context), started

    async def iter_stream(
        self,
        opened: OpenedInferenceStream,
        request: PreparedInferenceRequest,
        *,
        path: str,
        started: float,
    ) -> AsyncIterator[bytes]:
        error_code: str | None = None
        try:
            async for chunk in opened.response.aiter_raw():
                yield chunk
        except asyncio.CancelledError:
            error_code = "client_disconnected"
            raise
        except httpx.TimeoutException:
            error_code = "upstream_timeout"
        except httpx.HTTPError:
            error_code = "upstream_unavailable"
        finally:
            try:
                await opened.context.__aexit__(None, None, None)
            except httpx.HTTPError:
                pass
            if not opened.response.is_closed:
                await opened.response.aclose()
            self._log_completed(request, path, opened.response.status_code, started, error_code)

    def log_local_error(
        self,
        *,
        path: str,
        code: str,
        model: str | None = None,
        stream: bool | None = None,
    ) -> None:
        fields: dict[str, Any] = {"path": path, "error_code": code}
        if model is not None:
            fields["model"] = model
        if stream is not None:
            fields["stream"] = stream
        self._log.warning("inference_proxy.local_error", **fields)

    def _log_completed(
        self,
        request: PreparedInferenceRequest,
        path: str,
        status_code: int,
        started: float,
        error_code: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "path": path,
            "model": request.model,
            "stream": request.stream,
            "status_code": status_code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if error_code:
            fields["error_code"] = error_code
        self._log.info("inference_proxy.completed", **fields)
