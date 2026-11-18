from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone, tzinfo
from typing import Any


class OpenAPITimeParseError(ValueError):
    """Raised when an explicit public OpenAPI time value is invalid."""

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(
            f"{field_name} must be an ISO 8601 string or Unix millisecond timestamp"
        )


def get_present_param(
    params: Mapping[str, Any],
    *keys: str,
) -> tuple[str | None, Any]:
    """Return the first key that is present, preserving falsey explicit values."""
    for key in keys:
        if key in params:
            return key, params.get(key)
    return None, None


def parse_openapi_time(value: Any, *, field_name: str, tz: tzinfo) -> datetime:
    """Parse an explicit public OpenAPI time value.

    Accepted inputs are ISO 8601 strings and integer Unix milliseconds. Callers
    are responsible for handling omitted values before invoking this function.
    """
    if isinstance(value, bool):
        raise OpenAPITimeParseError(field_name)

    if isinstance(value, int):
        return _parse_unix_ms(value, field_name, tz)

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise OpenAPITimeParseError(field_name)
        if raw.isdigit():
            return _parse_unix_ms(int(raw), field_name, tz)
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise OpenAPITimeParseError(field_name) from exc
        return parsed.astimezone(tz) if parsed.tzinfo else parsed.replace(tzinfo=tz)

    raise OpenAPITimeParseError(field_name)


def _parse_unix_ms(value: int, field_name: str, tz: tzinfo) -> datetime:
    if value < 0:
        raise OpenAPITimeParseError(field_name)
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(tz)
    except (OverflowError, OSError, ValueError) as exc:
        raise OpenAPITimeParseError(field_name) from exc
