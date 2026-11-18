#!/usr/bin/env python3
"""Compatibility entry point; full-stack acceptance is owned by deploy/tests/."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "deploy/tests/seekdb_deploy_smoke.py"),
        run_name="__main__",
    )
