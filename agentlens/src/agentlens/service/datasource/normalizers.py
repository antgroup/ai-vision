from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime


def coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def coerce_int(value: object, default: int = 0) -> int:
    coerced = coerce_float(value)
    return int(coerced) if coerced is not None else default


def coerce_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1_000_000_000, tz=UTC)

    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            numeric = None
        if numeric is not None:
            return datetime.fromtimestamp(numeric / 1_000_000_000, tz=UTC)

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    return None


def unix_nano_to_iso8601(value: object) -> str | None:
    dt = coerce_datetime(value)
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def iso8601_to_unix_nano(value: str | None) -> int | None:
    if not value or not isinstance(value, str):
        return None
    dt = coerce_datetime(value)
    if dt is None:
        return None
    return int(dt.timestamp() * 1_000_000_000)


def collect_prefixed_values(row: Mapping[str, object], prefix: str) -> dict[str, object]:
    collected: dict[str, object] = {}
    for key, value in row.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        collected[key[len(prefix) :]] = value
    return collected


def build_metadata(row: Mapping[str, object]) -> dict[str, object]:
    fixed: dict[str, object] = {}
    fixed_candidates = {
        "kind": ("kind",),
        "status.code": ("status.code", "status_code"),
        "status.message": ("status.message", "status_message"),
    }
    for target_key, candidates in fixed_candidates.items():
        for candidate in candidates:
            if candidate in row:
                fixed[target_key] = row[candidate]
                break

    attributes = collect_prefixed_values(row, "attributes.")
    first_class_payload_attributes = {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "langfuse.observation.input",
        "langfuse.observation.output",
    }
    for key in first_class_payload_attributes:
        attributes.pop(key, None)

    langfuse_metadata_prefix = "langfuse.observation.metadata."
    for source_key in list(attributes.keys()):
        if not source_key.startswith(langfuse_metadata_prefix):
            continue
        target_key = source_key[len(langfuse_metadata_prefix) :]
        value = attributes.pop(source_key)
        if value not in (None, "", [], {}) and target_key not in attributes:
            attributes[target_key] = value

    metadata: dict[str, object] = {
        "attributes": attributes,
        "resourceAttributes": collect_prefixed_values(row, "resource.attributes."),
    }
    if fixed:
        metadata["fixed"] = fixed
    return metadata
