from __future__ import annotations

import logging
import logging.handlers
import os

from aivision_backend.logging_context import (
    RequestContextFormatter,
    get_error_log_handler,
    resolve_log_dir,
)

_BUSINESS_FILE_HANDLER_MARKER = "_aivision_business_file_handler"
_ERROR_HANDLER_MARKER = "_aivision_error_aggregation_handler_id"


def _has_owned_business_file_handler(
    logger: logging.Logger,
    filename: str,
) -> bool:
    return any(
        getattr(handler, _BUSINESS_FILE_HANDLER_MARKER, None) == filename
        for handler in logger.handlers
    )


def _has_error_aggregation_handler(
    logger: logging.Logger,
    error_handler: logging.Handler,
) -> bool:
    error_handler_id = id(error_handler)
    return any(
        handler is error_handler
        or getattr(handler, _ERROR_HANDLER_MARKER, None) == error_handler_id
        for handler in logger.handlers
    )


def _create_business_file_handler(filename: str) -> logging.Handler:
    log_dir = resolve_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, filename)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.set_name(f"aivision.business.{filename}")
    setattr(handler, _BUSINESS_FILE_HANDLER_MARKER, filename)
    handler.setFormatter(
        RequestContextFormatter(
            format="%(asctime)s %(request_context)s%(message)s",
            style="%",
        )
    )
    return handler


def _get_or_create_file_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not _has_owned_business_file_handler(logger, filename):
        logger.addHandler(_create_business_file_handler(filename))

    error_handler = get_error_log_handler()
    if not _has_error_aggregation_handler(logger, error_handler):
        error_handler.set_name("aivision.shared.error")
        setattr(error_handler, _ERROR_HANDLER_MARKER, id(error_handler))
        logger.addHandler(error_handler)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def get_ucsevent_logger() -> logging.Logger:
    return _get_or_create_file_logger("query_ucsevent", "query_ucsevent.log")


def get_trace_query_logger() -> logging.Logger:
    return _get_or_create_file_logger("query_traces", "query_traces.log")


def get_event_query_logger() -> logging.Logger:
    return _get_or_create_file_logger("query_events", "query_events.log")


def get_langfuse_logger() -> logging.Logger:
    return _get_or_create_file_logger("query_langfuse", "query_langfuse.log")


def get_openapi_access_logger() -> logging.Logger:
    return _get_or_create_file_logger("openapi_access", "openapi_access.log")


def get_platform_logger() -> logging.Logger:
    return _get_or_create_file_logger("platform_operation", "platform.log")


def get_query_logger() -> logging.Logger:
    """Backward-compatible alias — routes to stgtrace logger."""
    return get_trace_query_logger()
