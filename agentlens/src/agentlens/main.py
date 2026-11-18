import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

import agentlens.diagnoser  # noqa: F401 — auto-register executors
from agentlens.config import Settings
from agentlens.infra.ob.engine import probe_database
from agentlens.kernel.runtime import Runtime
from agentlens.logging import init_logging, shutdown_logging
from agentlens.service.inference.inference_proxy import InferenceProxyService
from agentlens.web.auth import TokenAuthMiddleware
from agentlens.web.deps import _engine_factory, close_antcollector_client, set_runtime
from agentlens.web.errors import install_error_handlers
from agentlens.web.routes.cluster import router as cluster_router
from agentlens.web.routes.dataset import router as dataset_router
from agentlens.web.routes.diagnoser import router as diagnoser_router
from agentlens.web.routes.diagnosis_record import router as record_router
from agentlens.web.routes.diagnosis_stats import router as diagnosis_stats_router
from agentlens.web.routes.inference_proxy import router as inference_proxy_router
from agentlens.web.routes.internal import drain_execute_tasks
from agentlens.web.routes.internal import router as internal_router
from agentlens.web.routes.job import router as job_router
from agentlens.web.routes.model_connection import router as model_connection_router
from agentlens.web.routes.quick_runs import diagnosis_runs_router
from agentlens.web.routes.quick_runs import router as quick_runs_router
from agentlens.web.routes.sandbox import router as sandbox_router
from agentlens.web.routes.specialist import router as specialist_router
from agentlens.web.routes.task import router as task_router
from agentlens.web.routes.trace import router as trace_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings  # read from app.state instead of re-creating
    init_logging(
        instance_uuid=settings.instance_uuid,
        level=settings.log_level,
        log_dir=settings.log_dir,
        log_stdout=settings.log_stdout,
        log_retention_days=settings.log_retention_days,
    )
    rt = Runtime(settings)
    inference_proxy = app.state.inference_proxy
    set_runtime(rt)
    await rt.start()
    try:
        yield
    finally:
        try:
            try:
                await rt.stop()
            finally:
                await drain_execute_tasks()
        finally:
            await asyncio.to_thread(close_antcollector_client)
            await inference_proxy.aclose()
            set_runtime(None)
            shutdown_logging()


def build_app() -> FastAPI:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    settings.log_loaded_config()
    app = FastAPI(title="AgentLens", lifespan=lifespan)
    app.state.settings = settings  # store for lifespan to use
    app.state.inference_proxy = InferenceProxyService()
    app.add_middleware(TokenAuthMiddleware, expected_token=settings.token)
    install_error_handlers(app)

    @app.get("/internal/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/internal/readyz")
    def readyz():
        try:
            with _engine_factory()() as session:
                if not probe_database(session.get_bind()):
                    return JSONResponse({"status": "unavailable"}, status_code=503)
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    @app.get("/internal/config")
    def config_view():
        return settings.to_safe_dict()

    for r in (
        specialist_router,
        diagnoser_router,
        dataset_router,
        job_router,
        task_router,
        record_router,
        diagnosis_stats_router,
        cluster_router,
        model_connection_router,
        sandbox_router,
        inference_proxy_router,
        internal_router,
        trace_router,
        quick_runs_router,
        diagnosis_runs_router,
    ):
        app.include_router(r)
    return app


# uvicorn entry (factory pattern): `uvicorn --factory agentlens.main:build_app`
