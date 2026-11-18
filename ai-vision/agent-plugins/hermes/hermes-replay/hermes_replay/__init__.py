"""Hermes replay plugin: explicit raw capture only."""

from __future__ import annotations

import atexit
import sys
import threading
from typing import Any

from .capture import RawEnvelopeWriter
from .config import ReplayConfig

HOOKS = (
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
    "pre_llm_call",
    "post_llm_call",
    "pre_api_request",
    "post_api_request",
    "api_request_error",
    "pre_tool_call",
    "post_tool_call",
    "subagent_start",
    "subagent_stop",
)
_writers: dict[str, RawEnvelopeWriter] = {}
_lock = threading.Lock()


def _shutdown() -> None:
    with _lock:
        writers = list(_writers.values())
        _writers.clear()
    for writer in writers:
        writer.close()


def _session_id(raw: dict[str, Any]) -> str:
    return str(
        raw.get("session_id")
        or raw.get("parent_session_id")
        or raw.get("child_session_id")
        or ""
    ).strip()


def register(ctx: Any) -> None:
    config = ReplayConfig.load()
    if not config.capture_enabled:
        return
    session_dir = config.session_dir
    assert session_dir is not None

    def callback(hook: str):
        def capture(**kwargs: Any) -> None:
            session_id = _session_id(kwargs)
            if not session_id:
                return
            with _lock:
                writer = _writers.get(session_id)
                if writer is None:
                    writer = RawEnvelopeWriter(
                        session_dir,
                        session_id,
                        collection_mode=config.collection_mode,
                        session_name=config.session_name,
                    )
                    _writers[session_id] = writer
            writer.append(hook, kwargs)

        return capture

    host_hooks = getattr(sys.modules.get("hermes_cli.plugins"), "VALID_HOOKS", ())
    hooks = (*HOOKS, *(hook for hook in ("pre_auxiliary_call", "post_auxiliary_call") if hook in host_hooks))
    for hook in hooks:
        ctx.register_hook(hook, callback(hook))
    atexit.register(_shutdown)
