#!/usr/bin/env python3
"""Describe the fixed optional runtime without importing the control-plane package."""

import argparse
import hashlib
import json
from pathlib import Path
import re

PACKAGES = {"pytest", "numpy", "pandas", "pydantic", "jsonschema", "openpyxl"}


def create_manifest(requirements: Path) -> dict:
    packages = {}
    for line in requirements.read_text().splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)\s*(?:\\)?", line)
        if match is None or match[1] not in PACKAGES:
            continue
        if match[1] in packages:
            raise ValueError("required runtime package has ambiguous pins")
        packages[match[1]] = match[2]
    if set(packages) != PACKAGES:
        raise ValueError("lock must pin all six runtime packages for CPython 3.11 Linux")
    manifest = {
        "id": "python-eval-v1",
        "python_version": "3.11.14",
        "packages": packages,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--digest-only", action="store_true")
    args = parser.parse_args()
    manifest = create_manifest(args.requirements)
    if args.digest_only:
        print(manifest["manifest_sha256"])
    elif args.output:
        args.output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    else:
        print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
