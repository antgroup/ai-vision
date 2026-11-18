"""Thin Hermes v0.18.2 turn and API lifecycle adapters."""

from __future__ import annotations

from typing import Any

from ..pipeline import submit_hook
from .usage import apply_reported_usage, discard_reported_usage


def pre_llm_call(
    runtime: Any, session_id: str = "", user_message: str = "",
    conversation_history: list[Any] | None = None, is_first_turn: bool = False,
    model: str = "", platform: str = "", **kwargs: Any,
) -> None:
    submit_hook(runtime, "pre_llm_call", {
        "session_id": session_id, "user_message": user_message,
        "conversation_history": conversation_history if conversation_history is not None else [],
        "is_first_turn": is_first_turn, "model": model, "platform": platform, **kwargs,
    })


def post_llm_call(
    runtime: Any, session_id: str = "", user_message: str = "",
    assistant_response: str = "", conversation_history: list[Any] | None = None,
    model: str = "", platform: str = "", **kwargs: Any,
) -> None:
    submit_hook(runtime, "post_llm_call", {
        "session_id": session_id, "user_message": user_message,
        "assistant_response": assistant_response,
        "conversation_history": conversation_history if conversation_history is not None else [],
        "model": model, "platform": platform, **kwargs,
    })


def pre_api_request(runtime: Any, **kwargs: Any) -> None:
    submit_hook(runtime, "pre_api_request", kwargs)


def post_api_request(runtime: Any, **kwargs: Any) -> None:
    submit_hook(runtime, "post_api_request", apply_reported_usage(runtime, kwargs))


def api_request_error(runtime: Any, **kwargs: Any) -> None:
    discard_reported_usage(runtime, str(kwargs.get("session_id") or ""), str(kwargs.get("api_request_id") or ""))
    submit_hook(runtime, "api_request_error", kwargs)


def pre_auxiliary_call(runtime: Any, **kwargs: Any) -> None:
    submit_hook(runtime, "pre_auxiliary_call", kwargs)


def post_auxiliary_call(runtime: Any, **kwargs: Any) -> None:
    submit_hook(runtime, "post_auxiliary_call", kwargs)
