"""Thin Hermes v0.18.2 subagent lifecycle adapters."""

from __future__ import annotations

from typing import Any

from ..pipeline import submit_hook


def on_subagent_start(runtime: Any, **kwargs: Any) -> None:
    submit_hook(runtime, "subagent_start", kwargs)


def on_subagent_stop(runtime: Any, **kwargs: Any) -> None:
    submit_hook(runtime, "subagent_stop", kwargs)
