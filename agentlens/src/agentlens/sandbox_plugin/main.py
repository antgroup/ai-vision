"""uvicorn agentlens.sandbox_plugin.main:app runs independently of AgentLens DB startup."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from agentlens.sandbox_plugin.config import PluginSettings
from agentlens.sandbox_plugin.environment import probe_environment
from agentlens.sandbox_plugin.provider import DockerCleanupVerifier, OpenShellProvider
from agentlens.sandbox_plugin.runtime import LeaseManager, PluginError


def _error(code: str, *, details=None) -> JSONResponse:
    error = PluginError(code, details=details)
    return JSONResponse(
        {
            "code": error.code,
            "msg": error.msg,
            **({"details": error.details} if error.details else {}),
        },
        status_code=error.status_code,
    )


async def _body(request: Request, limit: int) -> dict:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > limit:
            raise PluginError(
                "SANDBOX_INPUT_TOO_LARGE",
                details={
                    "stage": "request",
                    "actual_bytes": len(payload),
                    "allowed_bytes": limit,
                },
            )
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, RecursionError, UnicodeDecodeError):
        raise PluginError("SANDBOX_INPUT_INVALID") from None


def build_plugin_app(
    settings: PluginSettings | None = None,
    *,
    provider: Any = None,
    verifier: Any = None,
) -> FastAPI:
    """Explicit collaborators permit lifecycle tests without installing OpenShell."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            config = settings or PluginSettings.from_env()
            implementation = provider if provider is not None else OpenShellProvider(config)
            readback = verifier if verifier is not None else DockerCleanupVerifier(config)
            manager = LeaseManager(config, implementation, readback)
        except Exception:
            raise RuntimeError("Sandbox plugin operator configuration is invalid") from None
        application.state.settings = config
        application.state.manager = manager
        await run_in_threadpool(manager.recover_startup)
        stop = asyncio.Event()

        async def janitor():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=config.janitor_interval_seconds)
                except TimeoutError:
                    with contextlib.suppress(Exception):
                        await run_in_threadpool(manager.janitor_once, due_only=True)

        task = asyncio.create_task(janitor())
        try:
            yield
        finally:
            stop.set()
            await task
            await run_in_threadpool(manager.close)
            await run_in_threadpool(implementation.close)
            await run_in_threadpool(readback.close)

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @application.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path == "/health/live" and request.method == "GET":
            return await call_next(request)
        supplied = request.headers.get("authorization", "").encode("utf-8")
        expected = ("Bearer " + request.app.state.settings.token).encode("utf-8")
        if len(supplied) > 8200 or not hmac.compare_digest(supplied, expected):
            return _error("SANDBOX_UNAUTHORIZED")
        return await call_next(request)

    @application.exception_handler(PluginError)
    async def plugin_error(_request, error):
        return _error(error.code, details=error.details)

    @application.exception_handler(HTTPException)
    async def http_error(_request, _exception):
        return JSONResponse(
            {"code": "SANDBOX_ROUTE_UNAVAILABLE", "msg": "Sandbox route is unavailable"},
            status_code=404,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(_request, _exception):
        return _error("SANDBOX_INTERNAL_ERROR")

    @application.get("/health/live")
    async def live():
        return {"alive": True}

    async def health_response(manager: LeaseManager) -> dict:
        ready = await run_in_threadpool(manager.ready)
        status = manager.lease_status()
        manifests = await run_in_threadpool(manager.runtime_manifests)
        return {
            "protocol_version": 1,
            "provider": "openshell",
            "provider_version": manager.provider.version,
            "ready": ready and not status["draining"],
            "capabilities": [
                "custom_rule",
                "single_execution",
                "durable_leases",
                "verified_cleanup",
                "execution_input_preflight",
                "streaming_stdin",
                *(["python_eval_v1"] if manifests else []),
            ],
            "runtime_manifests": manifests,
            "max_input_bytes": manager.settings.max_input_bytes,
            "input_limits": await run_in_threadpool(manager.input_limits),
            "environment": await run_in_threadpool(
                probe_environment, manager.settings.docker_socket
            ),
            **status,
        }

    @application.get("/v1/health")
    async def health(request: Request):
        return await health_response(request.app.state.manager)

    @application.post("/v1/admin/drain")
    async def drain(request: Request):
        request.app.state.manager.drain()
        return await health_response(request.app.state.manager)

    @application.post("/v1/sandboxes")
    async def create(request: Request):
        data = await _body(request, request.app.state.settings.max_input_bytes)
        return await run_in_threadpool(request.app.state.manager.create, data)

    @application.post("/v1/executions/validate")
    async def validate_execution(request: Request):
        data = await _body(request, request.app.state.settings.max_input_bytes)
        return await run_in_threadpool(request.app.state.manager.validate_execution, data)

    @application.post("/v1/sandboxes/{lease_id}/execute")
    async def execute(lease_id: str, request: Request):
        data = await _body(request, request.app.state.settings.max_input_bytes)
        return await run_in_threadpool(request.app.state.manager.execute, lease_id, data)

    @application.delete("/v1/sandboxes/{lease_id}")
    async def delete(lease_id: str, request: Request):
        return await run_in_threadpool(request.app.state.manager.delete, lease_id)

    @application.get("/v1/sandboxes/{lease_id}")
    async def get(lease_id: str, request: Request):
        return await run_in_threadpool(request.app.state.manager.get, lease_id)

    return application


app = build_plugin_app()
