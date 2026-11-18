from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import threading
from contextvars import ContextVar, Token
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction

from .logging_fmt import OTelFormatter

_current_request: ContextVar[Any | None] = ContextVar(
    "aivision_current_request",
    default=None,
)
_error_handler: logging.Handler | None = None
_error_handler_lock = threading.Lock()


def set_current_request(request: Any) -> Token:
    return _current_request.set(request)


def reset_current_request(token: Token) -> None:
    _current_request.reset(token)


def _clean_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return (
        text[:128]
        .replace("\\", r"\\")
        .replace("\r", r"\r")
        .replace("\n", r"\n")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("|", r"\|")
    )


def _user_identifier(user: Any) -> str | None:
    if user is None:
        return None
    try:
        if not getattr(user, "is_authenticated", False):
            return None
        for attribute in ("uuid", "user_id", "username"):
            identifier = _clean_identifier(getattr(user, attribute, None))
            if identifier:
                return identifier
    except Exception:
        return None
    return None


def _record_user_identifier(
    record: logging.LogRecord,
    request: Any | None,
) -> str | None:
    for attribute in ("current_user", "user", "user_uuid", "user_id", "username"):
        value = getattr(record, attribute, None)
        if attribute == "user" and not isinstance(value, (str, int)):
            identifier = _user_identifier(value)
        else:
            identifier = _clean_identifier(value)
        if identifier:
            return identifier
    return _user_identifier(getattr(request, "user", None))


def _space_identifier(space: Any) -> str | None:
    if space is None:
        return None
    if isinstance(space, (str, int)):
        return _clean_identifier(space)
    for attribute in ("uuid", "space_id", "name"):
        identifier = _clean_identifier(getattr(space, attribute, None))
        if identifier:
            return identifier
    return None


def _mapping_space_identifier(mapping: Any) -> str | None:
    if not mapping:
        return None
    for key in ("space_id", "spaceId", "space_name"):
        try:
            identifier = _clean_identifier(mapping.get(key))
        except (AttributeError, TypeError):
            return None
        if identifier:
            return identifier
    return None


def _request_space_identifier(request: Any | None) -> str | None:
    if request is None:
        return None

    candidates = (request, getattr(request, "_request", None))
    for candidate in candidates:
        if candidate is None:
            continue
        resolver_match = getattr(candidate, "resolver_match", None)
        identifier = _mapping_space_identifier(getattr(resolver_match, "kwargs", None))
        if identifier:
            return identifier

        parser_context = getattr(candidate, "parser_context", None) or {}
        identifier = _mapping_space_identifier(parser_context.get("kwargs"))
        if identifier:
            return identifier

        identifier = _space_identifier(getattr(candidate, "space", None))
        if identifier:
            return identifier

        auth = getattr(candidate, "auth", None)
        identifier = _space_identifier(getattr(auth, "space", None))
        if identifier:
            return identifier

        for params_name in ("query_params", "GET"):
            identifier = _mapping_space_identifier(
                getattr(candidate, params_name, None)
            )
            if identifier:
                return identifier
    return None


def _record_space_identifier(
    record: logging.LogRecord,
    request: Any | None,
) -> str | None:
    for attribute in ("current_space", "space", "space_id", "space_name"):
        identifier = _space_identifier(getattr(record, attribute, None))
        if identifier:
            return identifier
    return _request_space_identifier(request)


def _record_request(record: logging.LogRecord) -> Any | None:
    request = getattr(record, "request", None)
    if request is not None and not isinstance(request, (str, bytes)):
        return request
    return _current_request.get()


def _request_context(record: logging.LogRecord) -> str:
    request = _record_request(record)
    has_explicit_context = any(
        getattr(record, attribute, None) is not None
        for attribute in ("current_user", "current_space")
    )
    if request is None and not has_explicit_context:
        return ""

    user_identifier = _record_user_identifier(record, request) or "-"
    space_identifier = _record_space_identifier(record, request) or "-"
    return f"[user:{user_identifier}|space:{space_identifier}] "


class RequestContextFormatter(OTelFormatter):
    """Add normalized user/space context while preserving native tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            record.request_context = _request_context(record)
        except Exception:
            # Logging must never mask the original application failure.
            record.request_context = ""
        return super().format(record)


class RequestLoggingContextMiddleware:
    """Expose the active request to logging formatters through a ContextVar."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        token = set_current_request(request)
        try:
            return self.get_response(request)
        finally:
            reset_current_request(token)

    async def __acall__(self, request):
        token = set_current_request(request)
        try:
            return await self.get_response(request)
        finally:
            reset_current_request(token)


def resolve_log_dir() -> str:
    configured_log_dir = os.environ.get("AIVISION_LOG_DIR")
    if configured_log_dir:
        return configured_log_dir

    if platform.system() == "Linux":
        return "/home/admin/logs/aivision"
    project_root = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
    return os.path.join(project_root, "logs")


def get_error_log_handler() -> logging.Handler:
    """Return one process-local handler shared by root and business loggers."""

    global _error_handler
    if _error_handler is not None:
        return _error_handler

    with _error_handler_lock:
        if _error_handler is None:
            log_dir = resolve_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, "error.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setLevel(logging.ERROR)
            handler.setFormatter(
                RequestContextFormatter(
                    format=(
                        "{levelname} {asctime} {name} "
                        "[trace_id={otelTraceID} span_id={otelSpanID}] "
                        "{request_context}{message}"
                    ),
                    style="{",
                )
            )
            _error_handler = handler
    return _error_handler
