"""Make the sibling core importable from an uninstalled source checkout."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def ensure_core_importable() -> None:
    if importlib.util.find_spec("aivision_hermes_core") is not None:
        return
    source_root = Path(__file__).resolve().parents[1] / "hermes-core"
    if source_root.is_dir():
        sys.path.insert(0, str(source_root))


ensure_core_importable()
