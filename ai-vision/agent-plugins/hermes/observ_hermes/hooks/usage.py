"""In-memory usage facts from the host's official provider execution middleware."""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("observ_hermes")
_lock = threading.RLock()
_ABSENT = object()
_LIMIT = 512
_COUNTERS = (
    "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _complete_length_response(runtime: Any, response: Any, fields: dict[str, Any], usage: Any, ended_at: float) -> None:
    """Settle a real output-cap response before Hermes starts its continuation.

    Hermes 0.18.2 skips post_api_request for text-length continuations. Its
    streaming transport also uses length on synthetic interrupted-stream
    stubs; those are deliberately left to the ordinary error/cancel lifecycle.
    No response or request is retained here, and projection/privacy still runs
    through the same canonical pipeline as the ordinary host callback.
    """
    if fields.get("api_mode") != "chat_completions":
        return
    if _field(response, "id") == "partial-stream-stub" or _field(response, "_content_filter_terminated", False):
        return
    choices = _field(response, "choices")
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        return
    choice = choices[0]
    message = _field(choice, "message")
    if _field(choice, "finish_reason") != "length" or message is None or _field(message, "tool_calls"):
        return
    # Only the documented assistant-message fields enter canonical telemetry;
    # transport metadata, provider headers and middleware context do not.
    assistant_message = {
        "role": _field(message, "role", "assistant"),
        "content": _field(message, "content"),
        "tool_calls": [],
    }
    reasoning_content = _field(message, "reasoning_content")
    if isinstance(reasoning_content, str):
        assistant_message["reasoning_content"] = reasoning_content
    context = {name: fields[name] for name in (
        "session_id", "api_request_id", "turn_id", "task_id", "model", "provider",
        "platform", "base_url", "api_mode", "api_call_count",
    ) if name in fields}
    from ..pipeline import submit_hook
    submit_hook(runtime, "post_api_request", {
        **context, "ended_at": ended_at, "finish_reason": "length",
        "response_model": _field(response, "model"),
        "reported_usage": usage, "assistant_message": assistant_message,
    })
    discard_reported_usage(runtime, *_key(fields))


def _counters(usage: dict[str, Any]) -> dict[str, Any]:
    def valid(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    result = {name: usage[name] for name in _COUNTERS if valid(usage.get(name))}
    for container, names in (("prompt_tokens_details", ("cached_tokens", "cache_write_tokens")), ("completion_tokens_details", ("reasoning_tokens",))):
        details = usage.get(container)
        retained = {name: details[name] for name in names if valid(details.get(name))} if isinstance(details, dict) else {}
        if retained:
            result[container] = retained
    return result


def _key(fields: dict[str, Any]) -> tuple[str, str]:
    return str(fields.get("session_id") or ""), str(fields.get("api_request_id") or "")


def observe_llm_execution(runtime: Any, request: Any, next_call: Any, **fields: Any) -> Any:
    # Discard any abandoned fact before the host retries the same call. Provider
    # exceptions and cancellation then propagate untouched.
    key = _key(fields)
    discard_reported_usage(runtime, *key)
    response = next_call(request)
    ended_at = time.time()
    if not all(key):
        return response
    try:
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if callable(getattr(usage, "model_dump", None)):
            usage = usage.model_dump(exclude_none=True, exclude_unset=True)
        elif usage is not None and not isinstance(usage, dict):
            usage = vars(usage)
        # Store only counters, never the full response or transport headers.
        usage = _counters(usage) if isinstance(usage, dict) else None
        with _lock:
            facts = getattr(runtime, "_observ_hermes_reported_usage", None)
            if facts is None:
                facts = OrderedDict()
                runtime._observ_hermes_reported_usage = facts
            facts[key] = usage
            facts.move_to_end(key)
            while len(facts) > _LIMIT:
                facts.popitem(last=False)
        _complete_length_response(runtime, response, fields, usage, ended_at)
    except Exception as error:
        logger.warning("[observ-hermes] usage capture failed error_type=%s", type(error).__name__)
    return response


def apply_reported_usage(runtime: Any, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        with _lock:
            facts = getattr(runtime, "_observ_hermes_reported_usage", {})
            usage = facts.pop(_key(fields), _ABSENT)
    except Exception as error:
        logger.warning("[observ-hermes] usage lookup failed error_type=%s", type(error).__name__)
        return fields
    return fields if usage is _ABSENT else {**fields, "reported_usage": usage}


def discard_reported_usage(runtime: Any, session_id: str, api_request_id: str = "") -> None:
    try:
        with _lock:
            facts = getattr(runtime, "_observ_hermes_reported_usage", {})
            for key in list(facts):
                if key[0] == session_id and (not api_request_id or key[1] == api_request_id):
                    del facts[key]
    except Exception as error:
        logger.warning("[observ-hermes] usage cleanup failed error_type=%s", type(error).__name__)
