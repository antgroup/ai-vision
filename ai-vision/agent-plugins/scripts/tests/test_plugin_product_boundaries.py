#!/usr/bin/env python3
"""Regression guards for production-package versus replay-package ownership."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


PRODUCTION_MANIFESTS = (
    ROOT / "opencode" / "observ-opencode" / "package.json",
    ROOT / "openclaw" / "observ-openclaw" / "package.json",
    ROOT / "pi" / "observ-pi" / "package.json",
)


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def replay_leaks(manifest: dict[str, Any]) -> list[str]:
    leaks: list[str] = []
    for entry in manifest.get("files", []):
        if "replay" in str(entry).lower():
            leaks.append(f"files:{entry}")
    for group in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
        "bundledDependencies",
        "bundleDependencies",
    ):
        values = manifest.get(group, {})
        names = values if isinstance(values, list) else values.keys()
        for name in names:
            if "replay" in str(name).lower():
                leaks.append(f"{group}:{name}")
    return leaks


class PluginProductBoundaryTests(unittest.TestCase):
    def test_production_manifests_do_not_ship_or_depend_on_replay(self) -> None:
        for path in PRODUCTION_MANIFESTS:
            with self.subTest(path=path):
                self.assertEqual(replay_leaks(load_manifest(path)), [])

    def test_pi_production_extension_list_contains_only_observ_pi(self) -> None:
        manifest = load_manifest(ROOT / "pi" / "observ-pi" / "package.json")
        self.assertEqual(manifest.get("pi", {}).get("extensions"), ["./index.js"])

    def test_guard_detects_file_and_dependency_leakage(self) -> None:
        fixture = {
            "files": ["dist", "dist/openclaw-replay.js"],
            "dependencies": {"opencode-replay": "1.0.0"},
        }
        self.assertEqual(
            replay_leaks(fixture),
            ["files:dist/openclaw-replay.js", "dependencies:opencode-replay"],
        )


if __name__ == "__main__":
    unittest.main()
