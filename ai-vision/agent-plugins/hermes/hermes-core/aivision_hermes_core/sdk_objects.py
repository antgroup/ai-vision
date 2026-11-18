"""Read the public response fields without importing or stringifying a host SDK.

The raw projection retains native field names. Message semantics are a separate
step, shared by live normalization and normalization of captured JSON.
"""
from typing import Any, Mapping


def _source_object(value: Any, fields: tuple[str, ...]) -> Any:
    if value is None or isinstance(value, (dict, list, str, bool, int, float)):
        return value
    return {name: getattr(value, name) for name in fields if hasattr(value, name)}


def response_object(value: Any) -> Any:
    if value is None or isinstance(value, (dict, str)):
        return value
    if not hasattr(value, "content") or not hasattr(value, "tool_calls"):
        return value
    source = _source_object(value, ("role", "content", "tool_calls", "finish_reason", "reasoning",
                                    "usage", "provider_data", "reasoning_content", "reasoning_details"))
    if isinstance(source.get("tool_calls"), (list, tuple)):
        calls = []
        for call in source["tool_calls"]:
            if isinstance(call, dict):
                calls.append(call)
            elif hasattr(call, "name") and hasattr(call, "arguments"):
                calls.append(_source_object(call, ("id", "name", "arguments", "provider_data")))
            else:
                item = _source_object(call, ("id", "type", "function"))
                if isinstance(item, dict) and "function" in item:
                    item["function"] = _source_object(item["function"], ("name", "arguments"))
                calls.append(item)
        source["tool_calls"] = calls
    if "usage" in source:
        source["usage"] = _source_object(source["usage"], (
            "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "request_count"))
    return source


def callback_objects(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: response_object(value) if key in {"assistant_message", "response"} else value
            for key, value in raw.items()}


def assistant_message(value: Any) -> Any:
    if not isinstance(value, dict) or "role" in value or not {"content", "tool_calls"} <= value.keys():
        return value
    message = {"role": "assistant", "content": value["content"], "tool_calls": []}
    for call in value.get("tool_calls") or []:
        if isinstance(call, dict) and {"name", "arguments"} <= call.keys():
            call = {"id": call.get("id"), "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]}}
        message["tool_calls"].append(call)
    for field in ("reasoning_content", "reasoning_details"):
        content = value.get(field) or (value.get("provider_data") or {}).get(field)
        if content is not None:
            message[field] = content
    if "reasoning_content" not in message and value.get("reasoning") is not None:
        message["reasoning_content"] = value["reasoning"]
    return message
