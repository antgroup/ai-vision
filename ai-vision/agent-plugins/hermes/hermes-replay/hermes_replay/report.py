"""Post replay OTLP JSONL files to AiVision, optionally timeshifted."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

TIMESTAMP_FIELDS = {
    "startTimeUnixNano",
    "endTimeUnixNano",
    "timeUnixNano",
    "observedTimeUnixNano",
}
REPLAY_PREFIXES = ("spans_", "traces_", "events_", "logs_", "metrics_")
LOOPBACK_BASE = "http://127.0.0.1:4319"


def validate_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 4319
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"replay report destination must be {LOOPBACK_BASE}")
    return LOOPBACK_BASE


def discover_replay_files(paths: Iterable[str | Path]) -> list[Path]:
    discovered: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            discovered.update(
                child
                for child in path.glob("*.jsonl")
                if child.name.startswith(REPLAY_PREFIXES)
            )
        elif path.is_file():
            discovered.add(path)
        else:
            raise FileNotFoundError(path)
    return sorted(discovered, key=lambda item: str(item))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: OTLP request must be an object")
        records.append(value)
    return records


def endpoint_for(path: Path, request: dict[str, Any]) -> str:
    if path.name.startswith(("spans_", "traces_")) or "resourceSpans" in request:
        return "/v1/traces"
    if path.name.startswith(("events_", "logs_")) or "resourceLogs" in request:
        return "/v1/logs"
    if path.name.startswith("metrics_") or "resourceMetrics" in request:
        return "/v1/metrics"
    raise ValueError(f"cannot infer OTLP signal for {path}")


def _timestamp_values(value: Any) -> Iterable[int]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in TIMESTAMP_FIELDS:
                try:
                    timestamp = int(child)
                except (TypeError, ValueError):
                    continue
                if timestamp > 0:
                    yield timestamp
            else:
                yield from _timestamp_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _timestamp_values(child)


def _apply_shift(value: Any, delta: int) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in TIMESTAMP_FIELDS:
                try:
                    timestamp = int(child)
                except (TypeError, ValueError):
                    continue
                if timestamp > 0:
                    value[key] = str(timestamp + delta)
            else:
                _apply_shift(child, delta)
    elif isinstance(value, list):
        for child in value:
            _apply_shift(child, delta)


def shift_requests(
    requests: Iterable[dict[str, Any]],
    *,
    target_unix_nano: int | None = None,
) -> list[dict[str, Any]]:
    """Shift only OTLP timestamp fields by one delta, preserving durations."""
    shifted = copy.deepcopy(list(requests))
    values = [
        timestamp for request in shifted for timestamp in _timestamp_values(request)
    ]
    if not values:
        return shifted
    target = target_unix_nano if target_unix_nano is not None else time.time_ns()
    delta = target - max(values)
    for request in shifted:
        _apply_shift(request, delta)
    return shifted


def shift_record_sets(
    record_sets: list[tuple[Path, list[dict[str, Any]]]],
    *,
    target_unix_nano: int | None = None,
) -> list[tuple[Path, list[dict[str, Any]]]]:
    """Apply one shared delta across traces, logs, and metrics."""
    shifted = [(path, copy.deepcopy(records)) for path, records in record_sets]
    values = [
        timestamp
        for _path, records in shifted
        for request in records
        for timestamp in _timestamp_values(request)
    ]
    if not values:
        return shifted
    target = target_unix_nano if target_unix_nano is not None else time.time_ns()
    delta = target - max(values)
    for _path, records in shifted:
        for request in records:
            _apply_shift(request, delta)
    return shifted


def write_shifted(
    files: list[Path], output_dir: str | Path, *, target_unix_nano: int | None = None
) -> list[Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    written: list[Path] = []
    record_sets = shift_record_sets(
        [(path, read_jsonl(path)) for path in files],
        target_unix_nano=target_unix_nano,
    )
    for path, records in record_sets:
        target = destination / path.name
        target.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        target.chmod(0o600)
        written.append(target)
    return written


def post_files(
    files: list[Path],
    *,
    base_url: str,
    shift: bool = False,
    timeout: float = 30.0,
) -> tuple[int, int]:
    posted = 0
    failed = 0
    base = validate_base_url(base_url)
    all_records = [(path, read_jsonl(path)) for path in files]
    if shift:
        all_records = shift_record_sets(all_records)
    for path, records in all_records:
        for request in records:
            endpoint = endpoint_for(path, request)
            payload = json.dumps(request, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
            http_request = urllib.request.Request(
                f"{base}{endpoint}",
                data=payload,
                headers={"content-type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(http_request, timeout=timeout) as response:
                    status = response.status
                    response.read()
            except urllib.error.HTTPError as error:
                status = error.code
                error.read()
                error.close()
            if 200 <= status < 300:
                posted += 1
            else:
                failed += 1
                print(
                    f"Report failed: {path} -> {endpoint} status={status}",
                    file=sys.stderr,
                )
    return posted, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Replay files or directories")
    parser.add_argument("--base-url", default=LOOPBACK_BASE)
    parser.add_argument("--shift", action="store_true")
    parser.add_argument(
        "--shift-output", help="Write shifted copies instead of posting"
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        files = discover_replay_files(args.paths)
        if not files:
            raise ValueError("no replay JSONL files found")
        if args.shift_output:
            written = write_shifted(files, args.shift_output)
            print(f"Shift complete: {len(written)} file(s) -> {args.shift_output}")
            return 0
        posted, failed = post_files(
            files,
            base_url=args.base_url,
            shift=args.shift,
            timeout=args.timeout,
        )
        base_url = args.base_url.rstrip("/")
        print(f"Report complete: posted={posted} failed={failed} base_url={base_url}")
        return 1 if failed else 0
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"Report failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
