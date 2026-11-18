"""Versioned canonical Hermes event model and checksum primitives."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .privacy import canonical_json

CANONICAL_SCHEMA_VERSION = 1
GENESIS_CHECKSUM = "0" * 64


class CanonicalFormatError(ValueError):
    """Raised when a canonical record cannot be trusted or migrated."""


@dataclass(frozen=True)
class CanonicalEvent:
    schema_version: int
    source_schema_version: str
    timestamp: str
    observed_at_unix_nano: str
    session_id: str
    event_id: str
    sequence: int
    previous_checksum: str
    checksum: str
    hook: str
    correlation: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CanonicalEvent":
        migrated = migrate_record(raw)
        required = {
            "schema_version",
            "source_schema_version",
            "timestamp",
            "observed_at_unix_nano",
            "session_id",
            "event_id",
            "sequence",
            "previous_checksum",
            "checksum",
            "hook",
            "correlation",
            "payload",
            "provenance",
            "projection",
        }
        missing = sorted(required - migrated.keys())
        if missing:
            raise CanonicalFormatError(
                f"canonical record missing fields: {', '.join(missing)}"
            )
        try:
            return cls(**{key: migrated[key] for key in required})
        except TypeError as exc:
            raise CanonicalFormatError(f"invalid canonical record: {exc}") from exc


def migrate_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Accept the current schema and its explicitly compatible 1.0 spelling."""
    record = dict(raw)
    version = record.get("schema_version")
    if version in (CANONICAL_SCHEMA_VERSION, "1", "1.0"):
        record["schema_version"] = CANONICAL_SCHEMA_VERSION
        return record
    raise CanonicalFormatError(
        f"unsupported Hermes canonical schema_version {version!r}; supported: 1"
    )


def checksum_base(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "checksum"}


def canonical_checksum(record: Mapping[str, Any]) -> str:
    base = checksum_base(record)
    previous = str(base.get("previous_checksum", ""))
    material = previous.encode("utf-8") + canonical_json(base).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def verify_event(event: CanonicalEvent) -> None:
    if event.schema_version != CANONICAL_SCHEMA_VERSION:
        raise CanonicalFormatError(
            f"unsupported canonical schema_version {event.schema_version!r}"
        )
    if not event.session_id:
        raise CanonicalFormatError("canonical session_id must be non-empty")
    if not event.hook:
        raise CanonicalFormatError("canonical hook must be non-empty")
    if event.sequence < 1:
        raise CanonicalFormatError("canonical sequence must be positive")
    if len(event.previous_checksum) != 64 or len(event.checksum) != 64:
        raise CanonicalFormatError(
            "canonical checksums must be 64 hexadecimal characters"
        )
    if not isinstance(event.timestamp, str):
        raise CanonicalFormatError("canonical timestamp must be an ISO-8601 string")
    try:
        parsed_timestamp = datetime.fromisoformat(
            event.timestamp.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise CanonicalFormatError(
            "canonical timestamp must be valid ISO-8601"
        ) from exc
    if parsed_timestamp.tzinfo is None:
        raise CanonicalFormatError("canonical timestamp must include a timezone")
    try:
        observed_at = int(event.observed_at_unix_nano)
    except (TypeError, ValueError) as exc:
        raise CanonicalFormatError(
            "canonical observed_at_unix_nano must be an integer"
        ) from exc
    if observed_at < 0:
        raise CanonicalFormatError(
            "canonical observed_at_unix_nano must be non-negative"
        )
    if canonical_checksum(event.to_dict()) != event.checksum:
        raise CanonicalFormatError(
            f"canonical checksum mismatch at sequence {event.sequence}"
        )
