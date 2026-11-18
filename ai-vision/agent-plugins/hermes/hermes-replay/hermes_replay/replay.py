"""Offline normalize and replay of hermes-replay raw callback envelopes."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from aivision_hermes_core.normalize import CanonicalNormalizer
from aivision_hermes_core.resource import build_resource_attributes
from aivision_hermes_core.semantic import (
    SemanticConfig,
    SemanticEngine,
    SemanticObservation,
)

from .capture import RawTranscriptError, read_raw_transcript
from .semantic_file import SemanticFileSink


def replay_suffix(path: Path) -> str:
    return f"{path.stem}_{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}"


def replay_envelopes(
    envelopes: list[dict],
    sink: Callable[[SemanticObservation], None],
) -> None:
    mode = str(envelopes[0].get("redaction", {}).get("collectionMode", "full"))
    # rawInput is already projected at the replay capture boundary.  In
    # privacy mode its content fields contain stable redaction markers, so the
    # offline normalizer must only sanitize them, never hide/hash them again.
    normalizer = CanonicalNormalizer("full")
    engine = SemanticEngine(
        SemanticConfig(agent_product="Hermes", collection_mode=mode),
        sink,
    )
    for envelope in envelopes:
        raw = envelope.get("rawInput")
        if not isinstance(raw, dict):
            raise RawTranscriptError("rawInput must be an object")
        event = normalizer.normalize(
            str(envelope.get("hook", "")),
            raw,
            timestamp=str(envelope.get("capturedAt", "")),
            provenance={
                "runtime": "hermes",
                "observer_schema": raw.get(
                    "telemetry_schema_version", "hermes.observer.v1"
                ),
                "replay_schema": envelope.get("schemaVersion"),
            },
        )
        engine.apply(event)
    engine.shutdown()


def replay_transcript(path: str | Path, output_dir: str | Path) -> dict[str, Path]:
    transcript = Path(path)
    envelopes = read_raw_transcript(transcript)
    mode = str(envelopes[0].get("redaction", {}).get("collectionMode", "full"))
    destination = Path(output_dir)
    existed = destination.exists()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix" and not existed:
        os.chmod(destination, 0o700)
    suffix = replay_suffix(transcript)
    published: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(
        prefix=".hermes-replay-", dir=destination
    ) as temporary:
        sink = SemanticFileSink(
            temporary,
            build_resource_attributes(
                service_name="hermes-replay",
                agent_type="hermes",
                agent_product="Hermes",
                agent_instance="replay",
                agent_env="Default",
                agent_scene="default",
                collection_mode=mode,
            ),
            suffix=suffix,
        )
        replay_envelopes(envelopes, sink)
        for signal, staged in sink.files.items():
            if staged.exists():
                target = destination / staged.name
                os.replace(staged, target)
                if os.name == "posix":
                    os.chmod(target, 0o600)
                published[signal] = target
    if not published:
        raise RuntimeError("replay did not produce semantic output")
    return published


def transcript_files(path: str | Path) -> list[Path]:
    candidate = Path(path)
    return (
        sorted(candidate.glob("session_*.jsonl")) if candidate.is_dir() else [candidate]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript")
    parser.add_argument("--otel", required=True)
    args = parser.parse_args(argv)
    inputs = transcript_files(args.transcript)
    if not inputs or any(not item.is_file() for item in inputs):
        print(
            f"Replay failed: no raw transcript found at {args.transcript}",
            file=sys.stderr,
        )
        return 1
    try:
        for transcript in inputs:
            outputs = replay_transcript(transcript, args.otel)
            destinations = ", ".join(map(str, outputs.values()))
            print(f"Replay complete: {transcript} -> {destinations}")
    except (OSError, RawTranscriptError, ValueError, RuntimeError) as error:
        print(f"Replay failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
