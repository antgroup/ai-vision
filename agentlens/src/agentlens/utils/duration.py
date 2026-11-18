import math
import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^(\d+)([hdm])$")
_UNIT_MAP = {"h": "hours", "d": "days", "m": "minutes"}


def parse_duration(value: str) -> timedelta:
    if not isinstance(value, str):
        raise ValueError(f"invalid duration: {value!r}, expected format like 30m/1h/1d")
    match = _DURATION_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid duration: {value!r}, expected format like 30m/1h/1d")
    amount, unit = int(match.group(1)), match.group(2)
    return timedelta(**{_UNIT_MAP[unit]: amount})


def normalize_hour_duration(value: str | None, *, default: str) -> str:
    duration = parse_duration(default if value is None else value)
    hours = max(1, math.ceil(duration.total_seconds() / 3600))
    return f"{hours}h"
