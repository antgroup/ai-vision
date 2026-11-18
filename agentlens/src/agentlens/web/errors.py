from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentlens.domain.errors import (
    Conflict,
    DomainError,
    ExternalUnavailable,
    Forbidden,
    InvalidTimeRange,
    NotFound,
    Validation,
)

_STATUS = [
    (NotFound, 404, "not_found"),
    (Conflict, 409, "conflict"),
    (Forbidden, 403, "forbidden"),
    (InvalidTimeRange, 400, "invalid_time_range"),
    (Validation, 422, "validation"),
    (ExternalUnavailable, 503, "external_unavailable"),
    (DomainError, 500, "internal"),
]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        for cls, status, code in _STATUS:
            if isinstance(exc, cls):
                return JSONResponse(
                    {"code": code, "msg": str(exc) or cls.__name__},
                    status_code=status,
                )
        return JSONResponse({"code": "internal", "msg": "internal"}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = exc.errors()
        parts = []
        for e in errors:
            loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
            parts.append(f"{loc}: {e.get('msg', 'invalid')}")
        msg = "; ".join(parts)
        return JSONResponse(
            {"code": "validation", "msg": msg},
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"code": "http_error", "msg": str(exc.detail)},
            status_code=exc.status_code,
        )
