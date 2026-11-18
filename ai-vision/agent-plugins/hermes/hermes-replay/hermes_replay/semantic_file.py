"""Private OTLP/JSON file sink for explicit replay-owned output."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from aivision_hermes_core.otlp_json import (
    OtlpJsonEncoder,
    attr_value,
    attributes,
    unix_nano,
)
from aivision_hermes_core.semantic import SemanticObservation

__all__ = ["SemanticFileSink", "attr_value", "attributes", "unix_nano"]


class SemanticFileSink:
    def __init__(
        self,
        output_dir: str | Path,
        resource: Mapping[str, Any],
        *,
        suffix: str = "live",
        aggregation_temporality: int = 2,
    ) -> None:
        self.output_dir = Path(output_dir)
        existed = self.output_dir.exists()
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix" and not existed:
            os.chmod(self.output_dir, 0o700)
        safe_suffix = (
            "".join(
                character if character.isalnum() or character in "._-" else "_"
                for character in suffix
            ).strip("_")
            or "replay"
        )
        self.files = {
            "span": self.output_dir / f"spans_{safe_suffix}.jsonl",
            "event": self.output_dir / f"events_{safe_suffix}.jsonl",
            "metric": self.output_dir / f"metrics_{safe_suffix}.jsonl",
            "diagnostic": self.output_dir / f"diagnostics_{safe_suffix}.ndjson",
        }
        self.encoder = OtlpJsonEncoder(
            resource,
            aggregation_temporality=aggregation_temporality,
        )

    def __call__(self, observation: SemanticObservation) -> None:
        encoded = self.encoder.encode(observation)
        if encoded is None:
            return
        signal, request = encoded
        self._append(signal, request)

    def abandon_pending(self) -> None:
        self.encoder.abandon_pending()

    def _append(self, signal: str, value: Mapping[str, Any]) -> None:
        file_key = {"traces": "span", "logs": "event", "metrics": "metric"}.get(
            signal, signal
        )
        path = self.files[file_key]
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        if os.name == "posix":
            os.chmod(path, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
