"""Thin Hermes v0.18.2 session lifecycle adapters."""

from __future__ import annotations

from typing import Any

from ..pipeline import submit_hook
from .usage import discard_reported_usage


def on_session_start(runtime: Any, session_id: str, model: str = "", platform: str = "", **kwargs: Any) -> None:
    submit_hook(runtime, "on_session_start", {"session_id": session_id, "model": model, "platform": platform, **kwargs})


def on_session_end(
    runtime: Any, session_id: str, completed: bool = False, interrupted: bool = False,
    model: str = "", platform: str = "", **kwargs: Any,
) -> None:
    discard_reported_usage(runtime, session_id)
    submit_hook(runtime, "on_session_end", {
        "session_id": session_id, "completed": completed, "interrupted": interrupted,
        "model": model, "platform": platform, **kwargs,
    })


def on_session_finalize(runtime: Any, session_id: str, platform: str = "", reason: str = "", **kwargs: Any) -> None:
    discard_reported_usage(runtime, session_id)
    submit_hook(runtime, "on_session_finalize", {
        "session_id": session_id, "platform": platform, "reason": reason, **kwargs,
    })


def on_session_reset(runtime: Any, session_id: str, platform: str = "", reason: str = "", **kwargs: Any) -> None:
    discard_reported_usage(runtime, session_id)
    submit_hook(runtime, "on_session_reset", {
        "session_id": session_id, "platform": platform, "reason": reason, **kwargs,
    })
