"""observ-hermes — OpenTelemetry observability plugin for Hermes Agent.

Hooks into Hermes lifecycle events and produces OTLP-compliant spans,
metrics, and events following aivision-agents semconv (C1-C6, C8-C11, C13).
"""

from __future__ import annotations

import atexit
import logging
import sys
import threading
from typing import TYPE_CHECKING, Any

from ._core_bootstrap import ensure_core_importable
from .config import ObservHermesConfig
from .diagnostics import close_diagnostics, configure_diagnostics

ensure_core_importable()

if TYPE_CHECKING:
    from .telemetry import TelemetryRuntime

logger = logging.getLogger(__name__)

_runtime: "TelemetryRuntime | None" = None
_hook_failure_lock = threading.Lock()


def init_telemetry(config: ObservHermesConfig) -> "TelemetryRuntime":
    """Lazy compatibility seam so config-only operations need no OTel import."""
    from .telemetry import init_telemetry as initialize

    return initialize(config)


def _record_hook_failure(runtime: Any, hook_name: str, error: Exception) -> None:
    """Keep a bounded, content-free failure counter beside structured logs."""
    key = f"{hook_name}:{type(error).__name__}"
    with _hook_failure_lock:
        failures = getattr(runtime, "_observ_hermes_hook_failures", None)
        if not isinstance(failures, dict):
            failures = {}
            setattr(runtime, "_observ_hermes_hook_failures", failures)
        count = int(failures.get(key, 0)) + 1
        failures[key] = count
    logger.warning(
        "[observ-hermes] hook failed hook=%s error_type=%s failure_count=%d",
        hook_name,
        type(error).__name__,
        count,
    )


def register(ctx: Any) -> None:
    """Hermes plugin entry point — registers all observability hooks."""
    global _runtime

    config = ObservHermesConfig.from_env()
    diagnostic_path = configure_diagnostics(config)
    if not config.enabled:
        logger.info("[observ-hermes] disabled via config")
        return

    logger.debug(
        "[observ-hermes] production diagnostics configured path=%s",
        str(diagnostic_path) if diagnostic_path else "disabled",
    )

    _runtime = init_telemetry(config)
    from .hooks.llm import (
        api_request_error,
        post_api_request,
        post_llm_call,
        pre_api_request,
        pre_llm_call,
        pre_auxiliary_call,
        post_auxiliary_call,
    )
    from .hooks.session import (
        on_session_end,
        on_session_finalize,
        on_session_reset,
        on_session_start,
    )
    from .hooks.subagent import on_subagent_start, on_subagent_stop
    from .hooks.tool import post_tool_call, pre_tool_call

    def _wrap(hook_name, hook_fn):
        """Wrap hook to inject runtime and catch errors."""

        def wrapper(**kwargs):
            try:
                return hook_fn(_runtime, **kwargs)
            except Exception as error:
                _record_hook_failure(_runtime, hook_name, error)

        return wrapper

    hooks = {
        "on_session_start": on_session_start,
        "on_session_end": on_session_end,
        "on_session_finalize": on_session_finalize,
        "on_session_reset": on_session_reset,
        "pre_llm_call": pre_llm_call,
        "post_llm_call": post_llm_call,
        "pre_api_request": pre_api_request,
        "post_api_request": post_api_request,
        "api_request_error": api_request_error,
        "pre_tool_call": pre_tool_call,
        "post_tool_call": post_tool_call,
        "subagent_start": on_subagent_start,
        "subagent_stop": on_subagent_stop,
    }
    host_hooks = getattr(sys.modules.get("hermes_cli.plugins"), "VALID_HOOKS", ())
    for hook_name, callback in {
        "pre_auxiliary_call": pre_auxiliary_call,
        "post_auxiliary_call": post_auxiliary_call,
    }.items():
        if hook_name in host_hooks:
            hooks[hook_name] = callback
    for hook_name, hook_fn in hooks.items():
        ctx.register_hook(hook_name, _wrap(hook_name, hook_fn))

    register_middleware = getattr(ctx, "register_middleware", None)
    if callable(register_middleware):
        from .hooks.usage import observe_llm_execution
        register_middleware("llm_execution", lambda **kwargs: observe_llm_execution(_runtime, **kwargs))

    atexit.register(_shutdown)
    logger.info(
        "[observ-hermes] registered (traces=%s, metrics=%s, events=%s, protocol=%s)",
        config.traces_enabled,
        config.metrics_enabled,
        config.events_enabled,
        config.otel_exporter.protocol,
    )


def _shutdown() -> None:
    global _runtime
    if _runtime:
        try:
            pipeline = getattr(_runtime, "_observ_hermes_pipeline", None)
            if pipeline is not None:
                pipeline.close()
            _runtime.force_flush()
            _runtime.shutdown()
        except Exception as error:
            logger.warning(
                "[observ-hermes] shutdown failed error_type=%s",
                type(error).__name__,
            )
        _runtime = None
    close_diagnostics()
