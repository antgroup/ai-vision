#!/usr/bin/env python3
"""Compatibility test entry point; deployment harness tests live in deploy/tests/."""

from pathlib import Path
import importlib.util
import runpy
import sys


TESTS = Path(__file__).resolve().parents[2] / "deploy/tests"


def load_tests(loader, tests, pattern):
    sys.path.insert(0, str(TESTS))
    spec = importlib.util.spec_from_file_location("canonical_deploy_smoke_tests", TESTS / "test_deploy_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)


if __name__ == "__main__":
    sys.path.insert(0, str(TESTS))
    runpy.run_path(str(TESTS / "test_deploy_smoke.py"), run_name="__main__")
