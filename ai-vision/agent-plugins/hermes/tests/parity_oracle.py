"""Fail-closed helpers for Hermes/OpenCode normalized signal parity fixtures."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PARITY_SCHEMA_VERSION = "aivision.signal-parity.v1"
SIGNAL_KINDS = frozenset({"metric", "span", "event", "resource"})
RECORD_KINDS = frozenset({"data_point", "span_start", "span_end", "event", "resource"})
INSTRUMENT_KINDS = frozenset({"counter", "histogram"})
STATUSES = frozenset({"OK", "ERROR"})
RECORD_FIELDS = frozenset(
    {
        "signal_kind",
        "record_kind",
        "name",
        "session_id",
        "turn_id",
        "conversation_id",
        "chat_id",
        "instrument_kind",
        "attributes",
        "value",
        "unit",
        "status",
        "producer",
    }
)
PRODUCER_FIELDS = frozenset(
    {
        "runtime",
        "version",
        "source_revision",
        "source",
        "generated_by",
    }
)


class ParityFixtureError(ValueError):
    """Raised when a parity fixture is malformed or incomplete."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ParityFixtureError(
            f"{label} fields mismatch: missing={missing}, extra={extra}"
        )


def validate_producer(producer: Any, *, label: str = "producer") -> dict[str, Any]:
    if not isinstance(producer, Mapping):
        raise ParityFixtureError(f"{label} must be an object")
    _require_exact_fields(producer, PRODUCER_FIELDS, label)
    normalized = dict(producer)
    if normalized["runtime"] not in {"hermes", "opencode"}:
        raise ParityFixtureError(f"{label}.runtime must be hermes or opencode")
    if normalized["source"] not in {"fixture", "live"}:
        raise ParityFixtureError(f"{label}.source must be fixture or live")
    for field in ("version", "source", "generated_by"):
        if not isinstance(normalized[field], str) or not normalized[field]:
            raise ParityFixtureError(f"{label}.{field} must be a non-empty string")
    revision = normalized["source_revision"]
    if revision is not None and (not isinstance(revision, str) or not revision):
        raise ParityFixtureError(
            f"{label}.source_revision must be null or a non-empty string"
        )
    return normalized


def validate_record(record: Any, *, index: int = 0) -> dict[str, Any]:
    label = f"records[{index}]"
    if not isinstance(record, Mapping):
        raise ParityFixtureError(f"{label} must be an object")
    _require_exact_fields(record, RECORD_FIELDS, label)
    normalized = dict(record)
    if normalized["signal_kind"] not in SIGNAL_KINDS:
        raise ParityFixtureError(f"{label}.signal_kind is invalid")
    if normalized["record_kind"] not in RECORD_KINDS:
        raise ParityFixtureError(f"{label}.record_kind is invalid")
    if not isinstance(normalized["name"], str) or not normalized["name"]:
        raise ParityFixtureError(f"{label}.name must be a non-empty string")
    if not isinstance(normalized["session_id"], str) or not normalized["session_id"]:
        raise ParityFixtureError(f"{label}.session_id must be a non-empty string")
    for field in ("turn_id", "conversation_id", "chat_id", "unit"):
        value = normalized[field]
        if value is not None and not isinstance(value, str):
            raise ParityFixtureError(f"{label}.{field} must be null or a string")
    instrument_kind = normalized["instrument_kind"]
    if instrument_kind is not None and instrument_kind not in INSTRUMENT_KINDS:
        raise ParityFixtureError(f"{label}.instrument_kind is invalid")
    status = normalized["status"]
    if status is not None and status not in STATUSES:
        raise ParityFixtureError(f"{label}.status is invalid")
    if not isinstance(normalized["attributes"], Mapping):
        raise ParityFixtureError(f"{label}.attributes must be an object")
    normalized["attributes"] = dict(normalized["attributes"])
    normalized["producer"] = validate_producer(
        normalized["producer"], label=f"{label}.producer"
    )
    return normalized


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityFixtureError(f"cannot load parity fixture {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ParityFixtureError(f"parity fixture {path} must contain an object")
    if document.get("schema_version") != PARITY_SCHEMA_VERSION:
        raise ParityFixtureError(
            f"parity fixture {path} must use schema_version {PARITY_SCHEMA_VERSION!r}"
        )
    producer = validate_producer(document.get("producer"))
    records = document.get("records")
    if not isinstance(records, list):
        raise ParityFixtureError(f"parity fixture {path} records must be an array")
    normalized_records = [
        validate_record(record, index=index) for index, record in enumerate(records)
    ]
    for index, record in enumerate(normalized_records):
        if record["producer"] != producer:
            raise ParityFixtureError(
                f"records[{index}].producer must equal the fixture producer"
            )
    return {**dict(document), "producer": producer, "records": normalized_records}


def record_sort_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    producer = record["producer"]
    return (
        record["session_id"],
        record["turn_id"] or "",
        record["signal_kind"],
        record["instrument_kind"] or "",
        record["name"],
        record["record_kind"],
        producer["runtime"],
        canonical_json(record["value"]),
        producer["source_revision"] or "",
        canonical_json(record["attributes"]),
    )


def normalized_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (validate_record(record, index=index) for index, record in enumerate(records)),
        key=record_sort_key,
    )


def _comparison_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {**dict(record), "producer": {}}


def _identity_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record["signal_kind"],
        record["record_kind"],
        record["name"],
        record["session_id"],
        record["turn_id"],
        record["conversation_id"],
        record["chat_id"],
        record["instrument_kind"],
        record["unit"],
    )


def compare_records(
    expected: Iterable[Mapping[str, Any]],
    actual: Iterable[Mapping[str, Any]],
    *,
    accepted_gap_names: Iterable[str] = (),
) -> dict[str, list[Any]]:
    """Return a deterministic structured diff without best-effort coercion."""
    gaps = frozenset(accepted_gap_names)
    expected_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    actual_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in normalized_records(expected):
        expected_groups[_identity_key(record)].append(record)
    for record in normalized_records(actual):
        actual_groups[_identity_key(record)].append(record)

    diff: dict[str, list[Any]] = {
        "added": [],
        "removed": [],
        "mismatched": [],
        "capability_gap_accepted": [],
    }
    for key in sorted(set(expected_groups) | set(actual_groups), key=canonical_json):
        expected_items = expected_groups.get(key, [])
        actual_items = actual_groups.get(key, [])
        common = min(len(expected_items), len(actual_items))
        for index in range(common):
            expected_payload = _comparison_payload(expected_items[index])
            actual_payload = _comparison_payload(actual_items[index])
            if expected_payload != actual_payload:
                bucket = "capability_gap_accepted" if key[2] in gaps else "mismatched"
                diff[bucket].append(
                    {"expected": expected_items[index], "actual": actual_items[index]}
                )
        for record in expected_items[common:]:
            bucket = "capability_gap_accepted" if record["name"] in gaps else "removed"
            diff[bucket].append(record)
        for record in actual_items[common:]:
            bucket = "capability_gap_accepted" if record["name"] in gaps else "added"
            diff[bucket].append(record)
    return diff
