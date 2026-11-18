"""Request-local canonical reuse and bounded, display-only IO previews.

The cache is a Python attribute, never a wire field or cross-request cache.
Only the verified-detail reader creates it; arbitrary telemetry cannot inject it.
"""
import json

from .semantics.observations import project_native_trace
from .semantics.usage import model_prices_fingerprint

PREVIEW_VERSION = "native-trace-preview-v1"
PREVIEW_CHARS = 1024


class ProjectedNativeDetail(dict):
    def __init__(self, detail, canonical, *, project_id, prices):
        super().__init__(detail)
        self.canonical = canonical
        self.project_id = str(project_id)
        self.prices_fingerprint = model_prices_fingerprint(prices)


def canonical_trace(detail, *, project_id, prices=None):
    if (isinstance(detail, ProjectedNativeDetail) and detail.project_id == str(project_id)
            and detail.prices_fingerprint == model_prices_fingerprint(prices)):
        return detail.canonical
    return project_native_trace(detail, project_id=project_id, prices=prices)


def io_text(value):
    return value if value is None or isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _content_text(content):
    if isinstance(content, str):
        return content if content else None
    if not isinstance(content, list) or not content:
        return None
    parts, unknown, media = [], [], False
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif (isinstance(part, dict) and part.get("type") in (None, "text", "input_text", "output_text")
              and isinstance(part.get("text"), str)):
            parts.append(part["text"])
        elif isinstance(part, dict) and isinstance(part.get("type"), str) and (
            part.get("type") in {"aivision_attachment", "aivision_media_error"}
            or (part.get("type") in {"image", "image_url", "input_image", "audio", "audio_url", "input_audio",
                                     "video", "video_url", "file", "document", "input_file", "media", "base64"}
                and (isinstance(part.get("source"), dict) or any(
                    isinstance(part.get(key), (str, dict)) for key in
                    ("data", "blob", "url", "file_url", "file_data", "file_id", "file", "image_url", "audio_url", "video_url", "input_audio"))))
        ):
            media = True
        else:
            unknown.append(part)
    if parts:
        return "\n".join(parts)
    if unknown:
        # Keep tool/structured fallback information without reintroducing media.
        return json.dumps(unknown, ensure_ascii=False) if media else None
    return ""


def message_content(raw):
    """Unbounded counterpart of the list's existing extractMessageContent.

    Preserve its last-message and observe(args) selection before truncating;
    slicing serialized history first produces invalid JSON and exposes a
    system-message prefix instead of the same message shown by legacy IO.
    """
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(value, list) and value:
        last = value[-1]
        text = _content_text(last.get("content")) if isinstance(last, dict) else None
        if text is not None and (text != "" or not any(key in last for key in ("tool_calls", "function_call", "refusal"))):
            return text
    if isinstance(value, dict):
        text = _content_text(value.get("content"))
        if text is not None and (text != "" or not any(key in value for key in ("tool_calls", "function_call", "refusal"))):
            return text
        if isinstance(value.get("args"), list):
            flat = [item for arg in value["args"] for item in (arg if isinstance(arg, list) else [arg])]
            parts = [arg if isinstance(arg, str) else arg.get("content", "") if isinstance(arg, dict) else "" for arg in flat]
            parts = [part for part in parts if isinstance(part, str) and part]
            if parts:
                return " › ".join(parts)
        messages = value.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            text = _content_text(last.get("content")) if isinstance(last, dict) else None
            if text is not None and (text != "" or not any(key in last for key in ("tool_calls", "function_call", "refusal"))):
                return text
    return raw


def _display_preview(value):
    from .media_visibility import redact_trace_media

    # Filter complete values before slicing: truncated serialized media cannot
    # be parsed by the final reader-permission filter. Keep canonical IO intact.
    raw = io_text(redact_trace_media(value))
    if raw in (None, ""):
        return raw, False
    display = message_content(raw)
    if display == "":
        return "", False
    encode = lambda count: json.dumps({"content": display[:count]}, ensure_ascii=False, separators=(",", ":"))
    # Keep a complete message object so the frontend extracts once, including
    # when the selected user's text itself happens to be valid JSON.
    low, high = 0, min(len(display), PREVIEW_CHARS)
    while low < high:
        middle = (low + high + 1) // 2
        if len(encode(middle)) <= PREVIEW_CHARS:
            low = middle
        else:
            high = middle - 1
    return encode(low), low < len(display)


def io_preview(trace):
    values = {field: _display_preview(trace[field]) for field in ("input", "output")}
    return {"version": PREVIEW_VERSION, "format": "message-content-v1", "complete": True,
            "rootObservationId": trace["rootObservationId"],
            **{field: value[0] for field, value in values.items()},
            **{field + "Truncated": value[1] for field, value in values.items()}}
