#!/usr/bin/env python3
"""Canonical paths for AiVision-owned transcripts, replays, and golden data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping, Sequence


ARTIFACT_KINDS = ("transcripts", "replays", "golden")
COMPONENTS = {
    "cc": "claudecode",
    "cdx": "codex",
    "ocw": "openclaw",
    "ocd": "opencode",
    "pi": "pi",
    "kimi": "kimi",
    "hermes": "hermes",
    "dsh": "deepseek",
}
ALIASES = {
    **{artifact_id: artifact_id for artifact_id in COMPONENTS},
    **{directory: artifact_id for artifact_id, directory in COMPONENTS.items()},
    "ocw-replay": "ocw",
    "ocd-replay": "ocd",
    "pi-replay": "pi",
    "km": "kimi",
    "hm": "hermes",
    "ds": "dsh",
    "dsh": "dsh",
    "deepseek": "dsh",
    "hms": "hermes",
}


def artifact_id(component: str) -> str:
    """Return the canonical artifact id for a component name or CLI alias."""
    try:
        return ALIASES[component]
    except KeyError as exc:
        supported = ", ".join(COMPONENTS)
        raise ValueError(f"unknown component {component!r}; expected one of: {supported}") from exc


def component_directory(component: str) -> str:
    return COMPONENTS[artifact_id(component)]


def aivision_home(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("AIVISION_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().absolute()

    if home is None:
        configured_home = env.get("HOME", "").strip()
        home = Path(configured_home).expanduser() if configured_home else Path.home()
    return (home / ".aivision").absolute()


def artifact_root(
    component: str,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return aivision_home(environ=environ, home=home) / artifact_id(component)


def artifact_path(
    component: str,
    kind: str,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    if kind not in ARTIFACT_KINDS:
        supported = ", ".join(ARTIFACT_KINDS)
        raise ValueError(f"unknown artifact kind {kind!r}; expected one of: {supported}")
    return artifact_root(component, environ=environ, home=home) / kind


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aivision-home",
        help="override AIVISION_HOME for this invocation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    path_parser = subparsers.add_parser("path", help="print one canonical path")
    path_parser.add_argument("component")
    path_parser.add_argument("kind", choices=ARTIFACT_KINDS)

    list_parser = subparsers.add_parser("list", help="list canonical paths")
    list_parser.add_argument("--component", action="append", default=[])
    list_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = dict(os.environ)
    if args.aivision_home:
        env["AIVISION_HOME"] = args.aivision_home

    if args.command == "path":
        print(artifact_path(args.component, args.kind, environ=env))
        return 0

    selected = args.component or list(COMPONENTS)
    rows = [
        {
            "component": artifact_id(component),
            "directory": component_directory(component),
            "kind": kind,
            "path": str(artifact_path(component, kind, environ=env)),
        }
        for component in selected
        for kind in ARTIFACT_KINDS
    ]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"{row['component']}\t{row['kind']}\t{row['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
