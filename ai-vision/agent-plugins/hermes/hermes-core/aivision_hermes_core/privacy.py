"""Secret-safe canonical payload projection helpers.

The transcript is a durable source artifact.  Values reaching it must be JSON
safe, bounded, and free from common credential material even in ``full`` mode.
``privacy`` mode additionally replaces content-bearing fields with stable
digest markers so correlations remain debuggable without retaining content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .media import media_block

REDACTED = "[REDACTED]"
MAX_STRING_LENGTH = 16_384
MAX_COLLECTION_ITEMS = 100
MAX_DEPTH = 12

_SECRET_KEY = re.compile(
    r"(?:api[-_]?key|authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|"
    r"access[-_]?token|refresh[-_]?token|token|secret|password|passwd|private[-_]?key|headers?)",
    re.IGNORECASE,
)
_PEM_PRIVATE_KEY_BEGIN = re.compile(
    r"-----BEGIN (?P<label>(?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY)-----"
)
_COOKIE_HEADER = re.compile(
    r"(?im)(\b(?:set-cookie|cookie)\s*:\s*)"
    r"(?=[^\r\n=;,\s]+\s*=)[^\r\n]*"
)
_SECRET_VALUE = re.compile(
    r"(?i)(\bbearer\s+)[a-z0-9._~+/=-]+|"
    r"(\bbasic\s+)[a-z0-9+/=]+|"
    r"(\bsk-)[a-z0-9_-]{12,}|"
    r"\b(?:gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,})\b|"
    r"\bglpat-[a-z0-9_-]{20,}\b|"
    r"\bxox[baprs]-[a-z0-9-]{10,}\b|"
    r"\b(?:akia|asia)[a-z0-9]{16}\b|"
    r"\baiza[a-z0-9_-]{20,}\b|"
    r"\b(?:sk|rk)_(?:live|test)_[a-z0-9]{16,}\b|"
    r"\b(?:hf|npm)_[a-z0-9]{20,}\b|"
    r"((?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|secret|"
    r"set[-_]?cookie|cookie)\s*[:=]\s*)[^\s,;]+"
)
_SAFE_NUMERIC_TOKEN_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


@dataclass(frozen=True)
class ProjectionResult:
    value: Any
    redacted: bool = False
    truncated: bool = False


def privacy_marker(value: Any) -> str:
    """Return a non-reversible, stable marker for intentionally hidden content."""
    serialized = canonical_json(json_safe(value))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return f"[REDACTED:sha256:{digest}]"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _redact_pem_private_keys(value: str) -> tuple[str, bool]:
    """Redact complete PEM blocks with a linear start/end-marker scan."""
    output: list[str] = []
    cursor = 0
    redacted = False
    while match := _PEM_PRIVATE_KEY_BEGIN.search(value, cursor):
        output.append(value[cursor : match.start()])
        output.append(REDACTED)
        redacted = True
        end_marker = f"-----END {match.group('label')}-----"
        end = value.find(end_marker, match.end())
        if end < 0:
            cursor = len(value)
            break
        cursor = end + len(end_marker)
    if not redacted:
        return value, False
    output.append(value[cursor:])
    return "".join(output), True


def _safe_string(value: str) -> ProjectionResult:
    safe, redacted = _redact_pem_private_keys(value)

    def replace(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        prefix = next((group for group in match.groups() if group), "")
        return f"{prefix}{REDACTED}"

    safe = _COOKIE_HEADER.sub(replace, safe)
    safe = _SECRET_VALUE.sub(replace, safe)
    truncated = len(safe) > MAX_STRING_LENGTH
    if truncated:
        safe = safe[:MAX_STRING_LENGTH] + "…"
    return ProjectionResult(safe, redacted=redacted, truncated=truncated)


def json_safe(value: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
    """Convert arbitrary values to deterministic JSON-compatible values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else str(value)
    if isinstance(value, bytes):
        return {
            "binary": True,
            "sha256": hashlib.sha256(value).hexdigest(),
            "length": len(value),
        }
    if _depth >= MAX_DEPTH:
        return "[MAX_DEPTH]"
    seen = set() if _seen is None else _seen
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return "[CIRCULAR]"
        seen.add(identity)
        result = {
            str(key): json_safe(item, _depth=_depth + 1, _seen=seen)
            for key, item in value.items()
        }
        seen.remove(identity)
        return result
    if isinstance(value, (list, tuple, set)):
        identity = id(value)
        if identity in seen:
            return "[CIRCULAR]"
        seen.add(identity)
        result = [json_safe(item, _depth=_depth + 1, _seen=seen) for item in value]
        seen.remove(identity)
        return result
    return str(value)


def sanitize(value: Any, *, sensitive_fields: Iterable[str] = ()) -> ProjectionResult:
    """Recursively redact secret-shaped keys/values and bound structures."""
    explicit = {field.casefold().replace("-", "_") for field in sensitive_fields}

    def walk(item: Any, depth: int, seen: set[int]) -> ProjectionResult:
        if isinstance(item, str):
            return _safe_string(item)
        if item is None or isinstance(item, (bool, int)):
            return ProjectionResult(item)
        if isinstance(item, float):
            return ProjectionResult(json_safe(item))
        if isinstance(item, bytes):
            return ProjectionResult(json_safe(item), truncated=True)
        if depth >= MAX_DEPTH:
            return ProjectionResult("[MAX_DEPTH]", truncated=True)
        if isinstance(item, dict):
            media = media_block(item)
            if media is not None:
                return ProjectionResult(media)
            identity = id(item)
            if identity in seen:
                return ProjectionResult("[CIRCULAR]", truncated=True)
            seen.add(identity)
            output: dict[str, Any] = {}
            redacted = False
            truncated = False
            entries = list(item.items())
            for raw_key, nested in entries[:MAX_COLLECTION_ITEMS]:
                key = str(raw_key)
                normalized = key.casefold().replace("-", "_")
                safe_count = (
                    normalized in _SAFE_NUMERIC_TOKEN_FIELDS
                    and isinstance(nested, (int, float))
                    and not isinstance(nested, bool)
                )
                if not safe_count and (
                    normalized in explicit or _SECRET_KEY.search(key)
                ):
                    output[key] = REDACTED
                    redacted = True
                    continue
                projected = walk(nested, depth + 1, seen)
                output[key] = projected.value
                redacted = redacted or projected.redacted
                truncated = truncated or projected.truncated
            if len(entries) > MAX_COLLECTION_ITEMS:
                output["_truncated_items"] = len(entries) - MAX_COLLECTION_ITEMS
                truncated = True
            seen.remove(identity)
            return ProjectionResult(output, redacted=redacted, truncated=truncated)
        if isinstance(item, (list, tuple, set)):
            identity = id(item)
            if identity in seen:
                return ProjectionResult("[CIRCULAR]", truncated=True)
            seen.add(identity)
            values = list(item)
            projected = [
                walk(nested, depth + 1, seen)
                for nested in values[:MAX_COLLECTION_ITEMS]
            ]
            seen.remove(identity)
            list_output = [entry.value for entry in projected]
            if len(values) > MAX_COLLECTION_ITEMS:
                list_output.append(
                    {"_truncated_items": len(values) - MAX_COLLECTION_ITEMS}
                )
            return ProjectionResult(
                list_output,
                redacted=any(entry.redacted for entry in projected),
                truncated=len(values) > MAX_COLLECTION_ITEMS
                or any(entry.truncated for entry in projected),
            )
        return _safe_string(str(item))

    return walk(value, 0, set())


def hide_content(value: Any) -> ProjectionResult:
    return ProjectionResult(privacy_marker(value), redacted=True)
