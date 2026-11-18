"""Validate structured dashboard time ranges."""

import time
from .errors import AgentQueryInvalidPayloadError

_RELATIVE_RANGE_WHITELIST = frozenset(
    {"5m", "15m", "30m", "1h", "3h", "6h", "12h", "24h", "7d", "30d"}
)

_MAX_TIME_RANGE_DAYS = 90

_RELATIVE_RANGE_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "3h": 10800,
    "6h": 21600,
    "12h": 43200,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}


def validate_time_range(time_range: dict) -> tuple[int, int]:
    """Validate and normalise a time range payload.

    Accepts either an ``absolute`` or ``relative`` time range dict and
    returns a ``(from_ms, to_ms)`` tuple of millisecond-precision UNIX
    timestamps.

    Raises:
        AgentQueryInvalidPayloadError: on any validation failure.
    """
    if not isinstance(time_range, dict):
        raise AgentQueryInvalidPayloadError(
            message="timeRange must be an object", details={}
        )
    range_type = time_range.get("type")

    if range_type == "absolute":
        from_val = time_range.get("from")
        to_val = time_range.get("to")

        if from_val is None or to_val is None:
            raise AgentQueryInvalidPayloadError(
                message="Absolute timeRange requires 'from' and 'to' fields",
                details={},
            )

        try:
            from_ms = int(from_val)
            to_ms = int(to_val)
        except (TypeError, ValueError) as exc:
            raise AgentQueryInvalidPayloadError(
                message="timeRange 'from' and 'to' must be numbers",
                details={},
            ) from exc

        if from_ms > to_ms:
            raise AgentQueryInvalidPayloadError(
                message="timeRange 'from' cannot be later than 'to'",
                details={},
            )

        span_ms = to_ms - from_ms
        span_days = span_ms / (24 * 3600 * 1000)
        if span_days > _MAX_TIME_RANGE_DAYS:
            raise AgentQueryInvalidPayloadError(
                message=f"Time range cannot exceed {_MAX_TIME_RANGE_DAYS} days",
                details={"span_days": round(span_days, 1)},
            )

        return from_ms, to_ms

    if range_type == "relative":
        range_val = time_range.get("range")
        if range_val not in _RELATIVE_RANGE_WHITELIST:
            raise AgentQueryInvalidPayloadError(
                message=f"Invalid relative timeRange: {range_val!r}",
                details={"range": range_val},
            )

        seconds = _RELATIVE_RANGE_SECONDS[range_val]
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - seconds * 1000
        return from_ms, now_ms

    raise AgentQueryInvalidPayloadError(
        message=f"Invalid timeRange type: {range_type!r}",
        details={"type": range_type},
    )
