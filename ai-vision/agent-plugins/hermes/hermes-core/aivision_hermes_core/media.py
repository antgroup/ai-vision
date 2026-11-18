"""Explicit media payload transport; SigNoz owns persistence and validation."""
from __future__ import annotations
import base64
import binascii
import re
from typing import Any

MAX_MEDIA_BYTES = 16 * 1024 * 1024
_TYPES = {"image", "audio", "video", "document", "file", "media", "base64", "text", "image_url", "input_image", "audio_url", "video_url", "input_audio", "input_file", "resource"}
_MIME = re.compile(r"(?:image/[a-z0-9.+-]+|audio/[a-z0-9.+-]+|video/[a-z0-9.+-]+|application/pdf|text/(?:plain|markdown|x-markdown))", re.I)


def media_block(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("type") == "aivision_media_error" and value.get("reason") in ("invalid", "budget"):
        return {"type": value["type"], "reason": value["reason"]}
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in _TYPES:
        return None
    source = value.get("source", value.get("resource", value.get("file", value)))
    if not isinstance(source, dict):
        return None
    mime = source.get("media_type", source.get("mime_type", source.get("mimeType", value.get("mime"))))
    data = source.get("data", source.get("blob", source.get("file_data")))
    url = source.get("url", source.get("file_url"))
    for field in ("image_url", "audio_url", "video_url"):
        candidate = value.get(field)
        if isinstance(candidate, dict):
            candidate = candidate.get("url")
        if isinstance(candidate, str):
            url = candidate
    if value.get("type") == "input_audio":
        source = value.get("input_audio", {})
        if not isinstance(source, dict):
            return {"type": "aivision_media_error", "reason": "invalid"}
        audio_format = source.get("format")
        mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac"}.get(audio_format) if isinstance(audio_format, str) else None
        if mime is None:
            return {"type": "aivision_media_error", "reason": "invalid"}
        data = source.get("data")
    if isinstance(data, str) and data.startswith("data:"):
        url = data
    if isinstance(url, str) and url.startswith("data:"):
        header, sep, body = url[5:].partition(";base64,")
        if not sep or (mime and str(mime).lower() != header.lower()):
            return {"type": "aivision_media_error", "reason": "invalid"}
        mime, data, url = header.lower(), body, None
    if isinstance(mime, str) and _MIME.fullmatch(mime) and isinstance(data, str):
        if len(data) > 4 * ((MAX_MEDIA_BYTES + 2) // 3):
            return {"type": "aivision_media_error", "reason": "budget"}
        try:
            decoded = base64.b64decode(data, validate=True)
            if not decoded:
                raise ValueError("Empty media")
        except (ValueError, binascii.Error):
            return {"type": "aivision_media_error", "reason": "invalid"}
        if len(decoded) > MAX_MEDIA_BYTES:
            return {"type": "aivision_media_error", "reason": "budget"}
        kind = mime.split("/")[0] if mime.startswith(("image/", "audio/", "video/", "text/")) else "document"
        return {"type": kind, "source": {"type": "base64", "media_type": mime.lower(), "data": data}}
    # External links still pass normal secret scrubbing and length limits.
    return None
