#!/usr/bin/env python3
"""Merge per-platform plugin dist trees (from parallel CI jobs) into one dist/.

build_plugins.py only merges ``latest.json`` when successive builds write into
the same directory. CI builds each platform on its own runner, so every job
uploads an independent ``dist/`` fragment. This script copies all fragments
into one tree and merges each plugin's ``latest.json.downloads`` list, keyed by
``(platform, arch)``.

Usage: merge_plugin_dist.py --output dist fragments/*/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


def merge_index(existing: dict | None, incoming: dict, key: str) -> dict:
    if existing is None:
        return {"version": incoming["version"], "downloads": list(incoming["downloads"])}
    if existing["version"] != incoming["version"]:
        raise SystemExit(f"{key}: version mismatch across fragments: "
                         f"{existing['version']} vs {incoming['version']}")
    seen = {(d["platform"], d["arch"]): d for d in existing["downloads"]}
    for item in incoming["downloads"]:
        target = item["platform"], item["arch"]
        if target in seen and seen[target] != item:
            raise SystemExit(f"{key}: conflicting artifacts for {target[0]}/{target[1]}")
        if target not in seen:
            existing["downloads"].append(item)
            seen[target] = item
    return existing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fragments", nargs="+", type=Path, help="dist/ fragment roots")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    indexes: dict[str, dict] = {}
    for fragment in args.fragments:
        if not fragment.is_dir():
            print(f"warn: skipping non-directory fragment {fragment}", file=sys.stderr)
            continue
        for plugin_dir in sorted(p for p in fragment.iterdir() if p.is_dir()):
            key = plugin_dir.name
            index_file = plugin_dir / "latest.json"
            if not index_file.is_file():
                continue
            indexes[key] = merge_index(indexes.get(key), json.loads(index_file.read_text()), key)
            for version_dir in (p for p in plugin_dir.iterdir() if p.is_dir()):
                destination = args.output / key / version_dir.name
                destination.mkdir(parents=True, exist_ok=True)
                for artifact in version_dir.iterdir():
                    if artifact.is_file():
                        shutil.copy2(artifact, destination / artifact.name)

    if not indexes:
        raise SystemExit("no plugin fragments found")

    for key, index in indexes.items():
        index["downloads"].sort(key=lambda d: (d["platform"], d["arch"]))
        (args.output / key / "latest.json").write_text(json.dumps(index, indent=2) + "\n")
        labels = ", ".join(f"{d['platform']}/{d['arch']}" for d in index["downloads"])
        print(f"{key} {index['version']}: {labels}")


if __name__ == "__main__":
    main()
