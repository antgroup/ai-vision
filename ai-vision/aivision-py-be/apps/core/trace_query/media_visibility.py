"""Apply media-read permissions to every representation returned by a query."""
from __future__ import annotations

import json
import re
from typing import Any

from apps.core.models import User
from apps.core.permissions import SpaceAuthorizationError, require_space_permission
from apps.core.permissions.constants import TELEMETRY_TRACE_READ_SENSITIVE


_MEDIA_TYPES = {
    "image", "audio", "video", "document", "file", "media", "base64",
    "image_url", "audio_url", "video_url", "input_image", "input_audio",
    "input_file", "aivision_attachment",
}
_MIME = re.compile(r"^(?:image/|audio/|video/|text/|application/(?:pdf|json)$)", re.I)
_DATA_URI = re.compile(r"data:[^,\s()\"'<>]+,[^\s)\"'<>]*", re.I)
_INLINE_IMAGE = re.compile(r"!\[(?:\\.|[^\]\\\n])*\]\((?:\\.|[^()\\\n]|\([^()\n]*\))*\)")
_REFERENCE_IMAGE = re.compile(r"!\[[^\]\n]*\](?:\[|\()")
_PAYLOAD_KEYS = {
    "data", "blob", "file_data", "url", "file_url", "imageUrl", "audioUrl",
    "videoUrl", "fileUrl",
}
_SOURCE_KEYS = {"source", "file", "resource", "input_audio", "image_url", "audio_url", "video_url"}
_URL_KEYS = {"image_url", "audio_url", "video_url"}


def _has_media_payload(value: dict[str, Any]) -> bool:
    if any(isinstance(value.get(key), str) for key in _PAYLOAD_KEYS):
        return True
    return any(
        (key in _URL_KEYS and isinstance(value.get(key), str))
        or (isinstance(value.get(key), dict) and _PAYLOAD_KEYS.intersection(value[key]))
        for key in _SOURCE_KEYS
    )


def media_redaction_required(space: Any, reader: Any) -> bool:
    # Machine reads and credential-signed legacy links have a separate scoped
    # authorization contract. Share users retain their signed local account UUID.
    if getattr(space, "pk", None) is None or not getattr(reader, "uuid", None):
        return False
    actor = reader if isinstance(reader, User) else User.objects.filter(
        uuid=reader.uuid, status=1, gmt_deleted__isnull=True,
    ).first()
    try:
        require_space_permission(
            user=actor, space_id=space.uuid,
            permission=TELEMETRY_TRACE_READ_SENSITIVE,
        )
        return False
    except SpaceAuthorizationError:
        return True


def redact_trace_media(value: Any, depth: int = 0) -> Any:
    """Keep text and metadata while removing typed bytes, links and raw copies.

    Raw OTLP attributes and legacy providers may contain JSON inside strings.
    Filtering at the shared service boundary covers those copies as well as
    canonical input/output, lists, sessions and downloadable detail responses.
    """
    if depth >= 64:
        return "[REDACTED]"
    if isinstance(value, dict):
        kind = value.get("type")
        mime = next((value.get(key) for key in ("mimeType", "mime_type", "media_type", "mimetype", "mime")
                     if isinstance(value.get(key), str)), "")
        if (kind == "aivision_attachment" and isinstance(value.get("id"), str)) or (
            ((isinstance(kind, str) and kind in _MEDIA_TYPES) or _MIME.match(mime))
            and _has_media_payload(value)
        ):
            return {"type": "aivision_media_error", "reason": "filtered"}
        return {key: redact_trace_media(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_trace_media(item, depth + 1) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[", '"')):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                filtered = redact_trace_media(decoded, depth + 1)
                if filtered != decoded:
                    return json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
        filtered = _INLINE_IMAGE.sub("[REDACTED]", value)
        # Reference images and more deeply nested destinations may carry their
        # target separately. Hide that field rather than leave a usable URL.
        if _REFERENCE_IMAGE.search(filtered) or (
            "![" in filtered and re.search(r"(?m)^\s*\[[^\]]+\]:", filtered)
        ):
            return "[REDACTED]"
        return _DATA_URI.sub("[REDACTED]", filtered)
    return value
