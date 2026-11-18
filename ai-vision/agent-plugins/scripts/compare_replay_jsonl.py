#!/usr/bin/env python3
"""Strict, record-oriented comparison for replay JSONL directories.

Each replay directory is compared by its direct ``*.jsonl`` file names.  Each
non-empty JSONL line is then parsed as an independent JSON root and compared
in order.  This gives transcript moves a deterministic, no-network parity
oracle without adding a runtime dependency.

Exit codes:
    0  file names and all records are equal after the requested ignores
    1  valid inputs differ
    2  command-line, pointer, or JSONL-shape usage error
    3  a baseline/candidate path or file cannot be read
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


EXIT_EQUAL = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_INPUT = 3

# An ignore pointer may not traverse any of these semantic segments.  The
# normalized aliases cover common OTLP spellings while keeping the rule
# independent of one particular envelope shape.
_PROTECTED_SEGMENTS = {
    "resourcespans",
    "scopespans",
    "spans",
    "traceid",
    "spanid",
    "parentspanid",
    "name",
    "kind",
    "status",
    "start",
    "end",
    "starttimeunixnano",
    "endtimeunixnano",
    "attributes",
    "events",
    "links",
    "message",
    "messages",
    "inputmessages",
    "outputmessages",
    "genaiinputmessages",
    "genaioutputmessages",
    "token",
    "tokens",
    "inputtoken",
    "outputtoken",
    "inputtokens",
    "outputtokens",
    "totaltokens",
    "usage",
}


class ComparatorUsageError(ValueError):
    """Raised when a pointer or JSONL shape violates the comparator contract."""


class ComparatorInputError(ValueError):
    """Raised when a path or file cannot be read."""


@dataclass(frozen=True)
class JsonlRecord:
    line_number: int
    value: Any


class _IgnoredValueType:
    """Sentinel that preserves the existence of an ignored JSON location."""


_IGNORED_VALUE = _IgnoredValueType()


def _normalize_segment(segment: str) -> str:
    return "".join(character for character in segment.lower() if character.isalnum())


def _is_protected_segment(segment: str) -> bool:
    normalized = _normalize_segment(segment)
    if normalized in _PROTECTED_SEGMENTS:
        return True

    # Namespaced message/token keys are common in OTLP JSON exports.
    return (
        normalized.endswith("inputmessages")
        or normalized.endswith("outputmessages")
        or normalized.endswith("inputtokens")
        or normalized.endswith("outputtokens")
        or normalized.endswith("totaltokens")
        or normalized.endswith("traceid")
        or normalized.endswith("spanid")
        or normalized.endswith("parentspanid")
    )


def decode_pointer(pointer: str) -> tuple[str, ...]:
    """Decode and validate one RFC 6901 JSON Pointer.

    The empty pointer would ignore an entire record, so it is deliberately
    rejected.  ``-`` is an RFC 6902 append token rather than an existing RFC
    6901 location and is rejected too.
    """

    if pointer == "":
        raise ComparatorUsageError("root pointer is not allowed")
    if not pointer.startswith("/"):
        raise ComparatorUsageError(f"pointer must start with '/': {pointer!r}")

    decoded: list[str] = []
    for raw_segment in pointer[1:].split("/"):
        segment: list[str] = []
        index = 0
        while index < len(raw_segment):
            character = raw_segment[index]
            if character != "~":
                segment.append(character)
                index += 1
                continue
            if index + 1 >= len(raw_segment) or raw_segment[index + 1] not in "01":
                raise ComparatorUsageError(
                    f"invalid RFC 6901 escape in pointer: {pointer!r}"
                )
            segment.append("/" if raw_segment[index + 1] == "1" else "~")
            index += 2

        decoded_segment = "".join(segment)
        if decoded_segment == "-":
            raise ComparatorUsageError(
                f"array append token '-' is not an existing JSON location: {pointer!r}"
            )
        if _is_protected_segment(decoded_segment):
            raise ComparatorUsageError(
                f"pointer traverses protected replay field {decoded_segment!r}: {pointer!r}"
            )
        decoded.append(decoded_segment)

    return tuple(decoded)


def _load_jsonl(path: Path) -> list[JsonlRecord]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ComparatorInputError(f"cannot read {path}: {error}") from error

    records: list[JsonlRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        # A final newline is not an additional record.  Empty lines are
        # ignored so tools that append a blank separator remain comparable.
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ComparatorUsageError(
                f"invalid JSONL in {path} at line {line_number}: {error.msg}"
            ) from error
        records.append(JsonlRecord(line_number=line_number, value=value))
    return records


def _direct_jsonl_files(directory: Path) -> dict[str, Path]:
    try:
        entries = list(directory.iterdir())
    except (OSError, UnicodeError) as error:
        raise ComparatorInputError(f"cannot read directory {directory}: {error}") from error

    files: dict[str, Path] = {}
    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError as error:
            raise ComparatorInputError(f"cannot inspect {entry}: {error}") from error
        if not is_file:
            raise ComparatorUsageError(
                f"replay directory contains a non-file entry: {entry}"
            )
        if entry.suffix != ".jsonl":
            raise ComparatorUsageError(
                f"replay directory contains a non-JSONL file: {entry}"
            )
        files[entry.name] = entry
    return files


def _load_replay_path(path: Path) -> dict[str, list[JsonlRecord]]:
    try:
        exists = path.exists()
    except OSError as error:
        raise ComparatorInputError(f"cannot inspect path {path}: {error}") from error
    if not exists:
        raise ComparatorInputError(f"path does not exist: {path}")

    try:
        is_directory = path.is_dir()
        is_file = path.is_file()
    except OSError as error:
        raise ComparatorInputError(f"cannot inspect path {path}: {error}") from error

    if is_directory:
        return {
            name: _load_jsonl(file_path)
            for name, file_path in sorted(_direct_jsonl_files(path).items())
        }
    if is_file:
        return {path.name: _load_jsonl(path)}
    raise ComparatorInputError(f"path is neither a regular file nor directory: {path}")


def _is_array_index(segment: str, length: int) -> bool:
    if not segment or (segment == "0" and len(segment) > 1):
        return False
    if not segment.isdigit():
        return False
    return int(segment) < length


def _mask_path(value: Any, pointer: Sequence[str]) -> tuple[Any, bool]:
    """Mask one path while preserving whether that path exists.

    The existence bit is important: a pointer may be a no-op only when both
    records lack the path.  If a field exists on one side only, the sentinel
    keeps that structural difference visible and the comparator returns 1.
    """

    if not pointer:
        return _IGNORED_VALUE, True

    head, *tail = pointer
    if isinstance(value, dict):
        if head not in value:
            return value, False
        copied = dict(value)
        if tail:
            child, found = _mask_path(copied[head], tail)
            if not found:
                return value, False
            copied[head] = child
        else:
            copied[head] = _IGNORED_VALUE
        return copied, True

    if isinstance(value, list) and _is_array_index(head, len(value)):
        copied = list(value)
        index = int(head)
        if tail:
            child, found = _mask_path(copied[index], tail)
            if not found:
                return value, False
            copied[index] = child
        else:
            copied[index] = _IGNORED_VALUE
        return copied, True

    return value, False


def _apply_ignores(value: Any, pointers: Iterable[Sequence[str]]) -> Any:
    masked = value
    for pointer in pointers:
        masked, _found = _mask_path(masked, pointer)
    return masked


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _first_difference(left: Any, right: Any, path: str = "") -> str | None:
    if left is _IGNORED_VALUE or right is _IGNORED_VALUE:
        return None if left is _IGNORED_VALUE and right is _IGNORED_VALUE else path or "/"
    if type(left) is not type(right):
        return path or "/"
    if isinstance(left, dict):
        left_keys = set(left)
        right_keys = set(right)
        if left_keys != right_keys:
            missing = sorted(left_keys - right_keys)
            extra = sorted(right_keys - left_keys)
            detail = []
            if missing:
                detail.append(f"missing candidate keys {missing!r}")
            if extra:
                detail.append(f"extra candidate keys {extra!r}")
            return f"{path or '/'} ({'; '.join(detail)})"
        for key in sorted(left):
            difference = _first_difference(
                left[key], right[key], f"{path}/{_escape_pointer_token(key)}"
            )
            if difference is not None:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path or '/'} (array length {len(left)} != {len(right)})"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = _first_difference(left_item, right_item, f"{path}/{index}")
            if difference is not None:
                return difference
        return None
    if left != right:
        return path or "/"
    return None


def compare_records(
    baseline: Sequence[JsonlRecord],
    candidate: Sequence[JsonlRecord],
    pointers: Iterable[Sequence[str]],
    *,
    file_name: str = "<file>",
) -> str | None:
    if len(baseline) != len(candidate):
        return f"{file_name}: JSONL record count {len(baseline)} != {len(candidate)}"

    pointer_list = tuple(pointers)
    for baseline_record, candidate_record in zip(baseline, candidate):
        baseline_value = _apply_ignores(baseline_record.value, pointer_list)
        candidate_value = _apply_ignores(candidate_record.value, pointer_list)
        difference = _first_difference(baseline_value, candidate_value)
        if difference is not None:
            return (
                f"{file_name}: record {baseline_record.line_number}/"
                f"{candidate_record.line_number} differs at {difference}"
            )
    return None


def compare_replay_paths(
    baseline: Path,
    candidate: Path,
    pointers: Iterable[Sequence[str]],
) -> str | None:
    for path in (baseline, candidate):
        try:
            exists = path.exists()
        except OSError as error:
            raise ComparatorInputError(f"cannot inspect path {path}: {error}") from error
        if not exists:
            raise ComparatorInputError(f"path does not exist: {path}")

    try:
        baseline_is_directory = baseline.is_dir()
        candidate_is_directory = candidate.is_dir()
        baseline_is_file = baseline.is_file()
        candidate_is_file = candidate.is_file()
    except OSError as error:
        raise ComparatorInputError(f"cannot inspect replay paths: {error}") from error

    if baseline_is_file and candidate_is_file:
        return compare_records(
            _load_jsonl(baseline),
            _load_jsonl(candidate),
            pointers,
            file_name=baseline.name,
        )
    if baseline_is_directory != candidate_is_directory or baseline_is_file != candidate_is_file:
        raise ComparatorUsageError(
            "baseline and candidate must both be replay directories or both be JSONL files"
        )

    baseline_files = _load_replay_path(baseline)
    candidate_files = _load_replay_path(candidate)
    baseline_names = set(baseline_files)
    candidate_names = set(candidate_files)
    if baseline_names != candidate_names:
        missing = sorted(baseline_names - candidate_names)
        extra = sorted(candidate_names - baseline_names)
        detail = []
        if missing:
            detail.append(f"missing candidate file(s) {missing!r}")
        if extra:
            detail.append(f"extra candidate file(s) {extra!r}")
        return "; ".join(detail)

    for file_name in sorted(baseline_files):
        difference = compare_records(
            baseline_files[file_name],
            candidate_files[file_name],
            pointers,
            file_name=file_name,
        )
        if difference is not None:
            return difference
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strict per-record JSONL comparator for replay artifacts."
    )
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument(
        "--ignore-pointer",
        action="append",
        default=[],
        metavar="POINTER",
        help="RFC 6901 pointer to a non-protected value; repeatable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        # argparse uses 2 for command-line usage errors and 0 for --help.
        return EXIT_USAGE if error.code != 0 else EXIT_EQUAL

    try:
        pointers = [decode_pointer(pointer) for pointer in args.ignore_pointer]
        difference = compare_replay_paths(args.baseline, args.candidate, pointers)
    except ComparatorUsageError as error:
        print(f"USAGE ERROR: {error}", file=sys.stderr)
        return EXIT_USAGE
    except ComparatorInputError as error:
        print(f"INPUT ERROR: {error}", file=sys.stderr)
        return EXIT_INPUT

    if difference is None:
        print("EQUAL: replay file names and JSONL records match")
        return EXIT_EQUAL

    print(f"MISMATCH: {difference}", file=sys.stderr)
    return EXIT_MISMATCH


if __name__ == "__main__":
    raise SystemExit(main())
