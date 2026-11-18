"""Thin Hermes v0.18.2 tool lifecycle adapters."""

from __future__ import annotations

from typing import Any

from ..pipeline import submit_hook


_MISSING = object()


def pre_tool_call(
    runtime: Any, tool_name: str = "", args: Any = _MISSING,
    task_id: str = "", **kwargs: Any,
) -> None:
    payload = {"tool_name": tool_name, "task_id": task_id, **kwargs}
    if args is not _MISSING:
        payload["args"] = args
    submit_hook(runtime, "pre_tool_call", payload)


def post_tool_call(
    runtime: Any, tool_name: str = "", args: Any = _MISSING,
    result: Any = _MISSING, task_id: str = "", duration_ms: int | float = 0,
    **kwargs: Any,
) -> None:
    payload = {
        "tool_name": tool_name, "task_id": task_id, "duration_ms": duration_ms, **kwargs,
    }
    if args is not _MISSING:
        payload["args"] = args
    if result is not _MISSING:
        payload["result"] = result
    submit_hook(runtime, "post_tool_call", payload)
